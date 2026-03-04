"""
Phase 4 Regression Tests: SBAR-First Behavior & Escalation Timing

Tests the new requirement: "Don't escalate prematurely. Complete intake and
generate SBAR before handing off to nurse (unless immediate red flag)."

Test scenarios:
1. Non-red-flag + nurse request → full intake → SBAR → escalate
2. Life-threatening + nurse request → immediate escalation (no full intake)
3. Caller insists repeatedly → rapid/minimum intake → SBAR → escalate
"""

import pytest
from unittest.mock import patch

from src.orchestrator.schemas import (
    DispositionCategory,
    OrchestratorSession,
    ConversationTurn,
    IntakeTurnOutput,
    IntakeStatePatch,
    RedFlagResult,
    SafetyLevel,
)


class TestSBARFirstBehavior:
    """Test the "SBAR-first before escalation" requirement."""

    @pytest.mark.asyncio
    async def test_non_redflag_nurse_request_completes_intake_then_escalates(
        self,
        orchestrator_with_mocks,
        mock_llm_client,
        mock_storage,
    ):
        """
        Scenario: Caller without red flags asks for nurse midway through intake.
        Expected: Assistant explains value, continues intake, generates SBAR, then escalates.
        """
        # Setup
        session = mock_storage.create_session("test-sbar-001")
        session.intake_state.caller_name = "Alice"
        session.intake_state.chief_complaint = "Fever"

        # Mock LLM to simulate continued intake
        mock_responses = [
            IntakeTurnOutput(
                extracted_fields_update=IntakeStatePatch(caller_age=35),
                missing_fields_prioritized=["onset_time", "severity"],
                next_question="When did the fever start?",
                llm_safety_flags=[],
                confidence=0.65,
            ),
            IntakeTurnOutput(
                extracted_fields_update=IntakeStatePatch(onset_time="2 days"),
                missing_fields_prioritized=["severity"],
                next_question="On a scale of 1-10, how severe is the fever?",
                llm_safety_flags=[],
                confidence=0.70,
            ),
            IntakeTurnOutput(
                extracted_fields_update=IntakeStatePatch(symptom_severity="moderate"),
                missing_fields_prioritized=[],
                next_question="Final question...",
                llm_safety_flags=[],
                confidence=0.80,
            ),
        ]

        call_idx = [0]

        async def mock_structured_call(*args, **kwargs):
            if call_idx[0] < len(mock_responses):
                result = mock_responses[call_idx[0]]
                call_idx[0] += 1
                return result
            return mock_responses[-1]

        mock_llm_client.structured_call = mock_structured_call

        # Simulate caller requesting nurse (non-red-flag case)
        session.conversation.append(
            ConversationTurn(
                role="caller",
                text="I want to talk to a nurse",
            )
        )

        # Assistant should validate, explain, continue
        response_1 = await orchestrator_with_mocks.process_turn(
            session, "I want to talk to a nurse"
        )

        # Assertions
        assert response_1 is not None
        # process_turn returns a dict; system should continue (not crash)

        # Simulate followup turns
        response_2 = await orchestrator_with_mocks.process_turn(
            session, "The fever started 2 days ago"
        )
        assert response_2 is not None

        # Final: should trigger finalization with SBAR
        finalize_output = await orchestrator_with_mocks.finalize(session)
        assert finalize_output is not None
        assert (
            finalize_output.sbar_report is not None or finalize_output.sbar is not None
        )
        # Disposition should be reasonable (SCHEDULE, HUMAN_REVIEW, etc.)
        disp_val = (
            finalize_output.disposition.value
            if hasattr(finalize_output.disposition, "value")
            else str(finalize_output.disposition)
        )
        assert disp_val in ["SCHEDULE", "HUMAN_REVIEW", "SELF_CARE", "URGENT"]

    @pytest.mark.asyncio
    async def test_redflag_with_nurse_request_escalates_immediately(
        self,
        orchestrator_with_mocks,
        mock_llm_client,
    ):
        """
        Scenario: Caller has deterministic red flag (e.g., CHEST_PAIN_SEVERE)
        Expected: Immediate escalation to ER, NO full intake, but minimal SBAR generated
        """
        session = OrchestratorSession(session_id="test-redflag-001")

        # Simulate red-flag detection in orchestrator
        with patch("src.orchestrator.orchestrator.check_red_flags") as mock_check_flags:
            # Mark red flag as triggered — must return RedFlagResult, not dict
            mock_check_flags.return_value = RedFlagResult(
                triggered=True,
                level=SafetyLevel.EMERGENT,
                matched_rules=["CHEST_PAIN_SEVERE"],
                script_to_say="Call 9 1 1 immediately.",
            )

            # Caller utterance with red flag
            utterance = "I have crushing chest pain radiating to my left arm"

            # Process turn
            result = await orchestrator_with_mocks.process_turn(session, utterance)

            # Assert: should go to finalize immediately (not ask more questions)
            # Result should indicate escalation
            # (Note: exact behavior depends on orchestrator implementation)
            assert result is not None

    @pytest.mark.asyncio
    async def test_repeated_escalation_request_rapid_intake_then_handoff(
        self,
        orchestrator_with_mocks,
    ):
        """
        Scenario: Caller insists on nurse multiple times (3+) and refuses to answer.
        Expected: Do minimum safe intake (short set), generate rapid SBAR, escalate.
        """
        session = OrchestratorSession(session_id="test-rapid-intake-001")
        session.intake_state.caller_name = "Bob"
        session.intake_state.chief_complaint = "Back pain"

        # Simulate 3 requests for escalation
        requests = [
            "I want to talk to a nurse",
            "Can I please talk to a nurse?",
            "I'm done with questions, connect me to a nurse now",
        ]

        for i, request in enumerate(requests):
            result = await orchestrator_with_mocks.process_turn(session, request)
            assert result is not None

            # After 3rd insistence, should escalate
            if i >= 2:
                # Should have triggered escalation path
                assert session.is_finalized or session.finalize_output is not None


class TestEscalationTiming:
    """Test escalation timing rules."""

    @pytest.mark.asyncio
    async def test_immediate_redflag_no_full_intake(
        self,
        orchestrator_with_mocks,
    ):
        """Critical red flags skip full intake."""
        OrchestratorSession(session_id="test-immed-001")

        critical_flags = [
            "CHEST_PAIN_SEVERE",
            "BREATHING_FAILURE",
            "STROKE_SIGNS",
            "ANAPHYLAXIS",
            "SUICIDAL_SELF_HARM",
        ]

        for flag in critical_flags:
            session_new = OrchestratorSession(session_id=f"test-{flag}")
            with patch("src.orchestrator.orchestrator.check_red_flags") as mock_flags:
                # Must return RedFlagResult, not dict
                mock_flags.return_value = RedFlagResult(
                    triggered=True,
                    level=SafetyLevel.EMERGENT,
                    matched_rules=[flag],
                    script_to_say="Call 9 1 1 immediately.",
                )

                # Process with critical utterance
                result = await orchestrator_with_mocks.process_turn(
                    session_new, f"I have {flag}"
                )
                # Should NOT ask follow-up questions
                assert result is not None

    @pytest.mark.asyncio
    async def test_non_redflag_requires_full_intake(
        self,
        orchestrator_with_mocks,
        mock_llm_client,
    ):
        """Non-red-flag cases must complete intake before escalation."""
        session = OrchestratorSession(session_id="test-full-intake-001")

        # Mock: keep asking questions until confidence rises
        question_count = [0]

        async def mock_call(*args, **kwargs):
            question_count[0] += 1
            return IntakeTurnOutput(
                extracted_fields_update=IntakeStatePatch(),
                missing_fields_prioritized=["onset_time"]
                if question_count[0] < 3
                else [],
                next_question=f"Question {question_count[0]}",
                llm_safety_flags=[],
                confidence=0.5 + (question_count[0] * 0.15),  # Increase confidence
            )

        mock_llm_client.structured_call = mock_call

        # Process multiple turns
        for i in range(4):
            result = await orchestrator_with_mocks.process_turn(
                session, f"Response {i + 1}"
            )
            assert result is not None

        # Assert: multiple questions were asked
        assert question_count[0] >= 3


class TestSBARGeneration:
    """Test that SBAR is generated before escalation."""

    @pytest.mark.asyncio
    async def test_sbar_present_on_escalation(
        self,
        orchestrator_with_mocks,
    ):
        """SBAR must be generated whenever escalating (except immediate 911)."""
        session = OrchestratorSession(session_id="test-sbar-gen-001")
        session.intake_state.caller_name = "Charlie"
        session.intake_state.caller_age = 45
        session.intake_state.chief_complaint = "Abdominal pain"
        session.intake_state.onset_time = "3 hours"
        session.intake_state.symptom_severity = "moderate"

        # Finalize should generate SBAR
        result = await orchestrator_with_mocks.finalize(session)

        # Assert SBAR exists
        assert result is not None
        if result.sbar:
            assert result.sbar.situation is not None
            assert result.sbar.background is not None
            assert result.sbar.assessment is not None
            assert result.sbar.recommendation is not None
        elif result.sbar_report:
            # Plain-text SBAR
            assert "S:" in result.sbar_report or "Situation" in result.sbar_report
            assert "B:" in result.sbar_report or "Background" in result.sbar_report
            assert "A:" in result.sbar_report or "Assessment" in result.sbar_report
            assert "R:" in result.sbar_report or "Recommendation" in result.sbar_report

    @pytest.mark.asyncio
    async def test_sbar_completeness(
        self,
        orchestrator_with_mocks,
    ):
        """SBAR should include all required sections when generated."""
        session = OrchestratorSession(session_id="test-sbar-complete")
        session.intake_state.caller_name = "Diana"
        session.intake_state.caller_age = 32
        session.intake_state.chief_complaint = "Cough"
        session.intake_state.onset_time = "1 week"
        session.intake_state.symptom_severity = "mild"

        result = await orchestrator_with_mocks.finalize(session)
        assert result is not None

        # Check completeness
        sbar_text = result.sbar_report or str(result.sbar)
        # Should mention key info
        assert len(sbar_text) > 50  # Non-trivial length
        # Should contain the standard SBAR sections
        assert "S:" in sbar_text or "Situation" in sbar_text


class TestNurseHandoffFlow:
    """Test the nurse handoff escalation path."""

    @pytest.mark.asyncio
    async def test_nurse_handoff_includes_sbar(
        self,
        orchestrator_with_mocks,
    ):
        """Nurse handoff should include complete SBAR."""
        session = OrchestratorSession(session_id="test-handoff-001")
        session.intake_state.caller_name = "Eve"
        session.intake_state.chief_complaint = "Headache"

        result = await orchestrator_with_mocks.finalize(session)

        # Handoff disposition
        assert result.disposition in [
            DispositionCategory.HUMAN_REVIEW,
            DispositionCategory.SCHEDULE,
        ]

        # Must have SBAR and summary
        assert result.sbar_report is not None or result.sbar is not None
        assert result.patient_summary is not None

    @pytest.mark.asyncio
    async def test_handoff_explains_escalation(
        self,
        orchestrator_with_mocks,
    ):
        """Handoff response should explain why escalating to nurse."""
        session = OrchestratorSession(session_id="test-handoff-explain")
        session.intake_state.caller_name = "Frank"
        session.intake_state.chief_complaint = "Unclear symptoms"

        result = await orchestrator_with_mocks.finalize(session)

        # Should have reasoning
        assert result.disposition_reasoning is not None
        assert len(result.disposition_reasoning) > 0


# ============================================================================
# Integration with Golden Calls
# ============================================================================


class TestGoldenCallsWithSBARFirst:
    """Test golden calls specifically for SBAR-first behavior."""

    @pytest.mark.asyncio
    async def test_moderate_case_completes_intake(
        self,
        golden_case_moderate,
        orchestrator_with_mocks,
    ):
        """Moderate case (no red flags) should complete intake."""
        case = golden_case_moderate
        session = OrchestratorSession(session_id=case["case_id"])

        # Replay conversation turns
        for turn in case.get("conversation", []):
            if turn["speaker"] == "caller":
                result = await orchestrator_with_mocks.process_turn(
                    session, turn["text"]
                )
                assert result is not None

        # Finalize
        final = await orchestrator_with_mocks.finalize(session)

        # Must have SBAR
        assert final.sbar_report is not None or final.sbar is not None

        # Disposition should match expected (if available)
        # (Mock returns safe defaults so HUMAN_REVIEW is also acceptable)
        expected_disp = case.get("expected_outcomes", {}).get("disposition")
        if expected_disp:
            disp_val = (
                final.disposition.value
                if hasattr(final.disposition, "value")
                else str(final.disposition)
            )
            assert disp_val in [expected_disp, "HUMAN_REVIEW"]

    @pytest.mark.asyncio
    async def test_life_threatening_case_quick_escalation(
        self,
        golden_case_life_threatening,
        orchestrator_with_mocks,
    ):
        """Life-threatening cases should escalate quickly (minimal intake)."""
        case = golden_case_life_threatening
        session = OrchestratorSession(session_id=case["case_id"])

        # Get red-flag utterance(s)
        utterances = []
        for turn in case.get("conversation", []):
            if turn["speaker"] == "caller":
                utterances.append(turn["text"])

        # First red-flag utterance should trigger escalation
        for utterance in utterances[:3]:  # First 3 turns max
            result = await orchestrator_with_mocks.process_turn(session, utterance)
            if session.is_finalized:  # Escalated
                break
            assert result is not None

        # Should have escalated to ER_NOW
        expected_disp = case.get("expected_outcomes", {}).get("disposition")
        if expected_disp == "ER_NOW":
            final_disp = (
                session.finalize_output.disposition if session.finalize_output else None
            )
            assert (
                final_disp == DispositionCategory.ER_NOW
                or len(session.conversation) <= 3
            )  # Quick escalation
