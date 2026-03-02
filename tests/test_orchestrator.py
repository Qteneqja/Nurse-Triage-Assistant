"""
Tests for orchestrator routing decisions and finalize path.

Uses a mock LLM client to test orchestrator logic without real API calls.
Covers:
- Deterministic red flag triggers immediate escalation
- Normal turn flow (ask → ask → finalize)
- Max turns triggers finalize
- Confidence threshold triggers finalize
- Fallback on LLM failure
- Deterministic override of LLM disposition
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.schemas import (
    DispositionCategory,
    FinalizeOutput,
    IntakeTurnOutput,
    IntakeStatePatch,
    OrchestratorSession,
    AuditTrace,
    StructuredIntakeState,
    Phase1TurnOutput,
    Phase1Disposition,
    Phase1NextAction,
)
from src.llm.client import LLMCallError
import json as _json


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _valid_phase1_json() -> str:
    """Return a minimal valid Phase1TurnOutput JSON for mock _raw_call."""
    return _json.dumps({
        "confidence_score": 0.8,
        "escalation_required": False,
        "red_flags_triggered": [],
        "rules_triggered": [],
        "next_action": "ASK_QUESTION",
        "disposition": "UNDECIDED",
    })

def _valid_phase1_result(**overrides) -> "Phase1TurnOutput":
    """Return a valid Phase1TurnOutput for mock structured_call."""
    defaults = {
        "confidence_score": 0.8,
        "escalation_required": False,
        "red_flags_triggered": [],
        "rules_triggered": [],
        "next_action": Phase1NextAction.ASK_QUESTION,
        "disposition": Phase1Disposition.HUMAN_REVIEW,
    }
    defaults.update(overrides)
    return Phase1TurnOutput(**defaults)

def _make_session(**kwargs) -> OrchestratorSession:
    """Create a test orchestrator session."""
    defaults = {
        "session_id": "test-session-001",
        "max_turns": 12,
        "confidence_threshold": 0.75,
    }
    defaults.update(kwargs)
    return OrchestratorSession(**defaults)


def _make_intake_output(**kwargs) -> IntakeTurnOutput:
    """Create a test IntakeTurnOutput."""
    defaults = {
        "extracted_fields_update": IntakeStatePatch(),
        "missing_fields_prioritized": ["onset_time"],
        "next_question": "When did this start?",
        "llm_safety_flags": [],
        "confidence": 0.3,
    }
    defaults.update(kwargs)
    return IntakeTurnOutput(**defaults)


def _make_finalize_output(**kwargs) -> FinalizeOutput:
    """Create a test FinalizeOutput."""
    defaults = {
        "disposition": DispositionCategory.SCHEDULE,
        "disposition_reasoning": "Mild symptoms, no red flags",
        "safety_net_instructions": "Go to ER if symptoms worsen",
        "sbar_report": "S: Test\nB: Test\nA: Test\nR: Test",
        "patient_summary": "A nurse will contact you soon.",
        "llm_safety_flags": [],
    }
    defaults.update(kwargs)
    return FinalizeOutput(**defaults)


# -----------------------------------------------------------------------
# Test: Deterministic red flag escalation
# -----------------------------------------------------------------------

class TestDeterministicEscalation:
    @pytest.mark.asyncio
    async def test_breathing_emergency_triggers_escalation(self):
        """Utterance matching a red-flag rule should escalate immediately (no LLM call)."""
        mock_llm = MagicMock()
        mock_llm.call = AsyncMock()  # should NOT be called

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "I can't breathe at all")

        assert result["action"] == "escalate"
        assert "9 1 1" in result["message"]
        assert session.is_finalized is True

        # LLM should NOT have been called
        mock_llm.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_suicidal_content_escalates(self):
        """Suicidal content triggers immediate escalation with crisis line number."""
        mock_llm = MagicMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "I want to kill myself")

        assert result["action"] == "escalate"
        assert "9 8 8" in result["message"]
        mock_llm.call.assert_not_called()


# -----------------------------------------------------------------------
# Test: Normal questioning flow
# -----------------------------------------------------------------------

class TestNormalFlow:
    @pytest.mark.asyncio
    async def test_continue_asking(self):
        """Normal utterance with low confidence should continue asking."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.3)
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "I have a mild headache")

        assert result["action"] == "ask"
        assert result["message"] == "When did this start?"
        assert session.turn_count == 1
        assert mock_llm.call.call_count == 2

    @pytest.mark.asyncio
    async def test_extracted_fields_applied(self):
        """Extracted fields from LLM should be applied to session state."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(
            extracted_fields_update=IntakeStatePatch(
                chief_complaint="headache",
                symptom_severity="moderate",
            ),
            confidence=0.4,
        )
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        await orch.process_turn(session, "I have a moderate headache")

        assert session.intake_state.chief_complaint == "headache"
        assert session.intake_state.symptom_severity == "moderate"


# -----------------------------------------------------------------------
# Test: Finalize triggers
# -----------------------------------------------------------------------

def _make_complete_session(**kwargs) -> OrchestratorSession:
    """Create a session that has passed the minimum turn & field gates.

    This simulates a session that has already gone through 5+ turns and has
    all required SBAR fields populated, so finalization tests can exercise
    the confidence / finalize_ready / missing-fields logic without being
    blocked by the hard gates.
    """
    defaults = {
        "session_id": "test-session-001",
        "max_turns": 12,
        "confidence_threshold": 0.75,
        "intake_state": StructuredIntakeState(
            caller_age=45,
            caller_sex="male",
            chief_complaint="persistent headache",
            onset_time="3 days ago",
            symptom_severity="moderate",
            relevant_history=["migraines"],
            meds=["ibuprofen"],
            allergies=["penicillin"],
        ),
    }
    defaults.update(kwargs)
    session = OrchestratorSession(**defaults)
    # Simulate 7 prior turns so we pass the minimum-turn gate.
    session.turn_count = 7
    return session


class TestFinalizeDecision:
    @pytest.mark.asyncio
    async def test_high_confidence_triggers_finalize(self):
        """Confidence >= threshold should trigger finalization when SBAR fields are present."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.85)
        # finalize() is no longer called inline in process_turn — it is
        # deferred to the route's background task to avoid blocking the
        # Twilio HTTP response past the 15-second timeout.  Only two LLM
        # calls happen in process_turn: phase1 + intake (via asyncio.gather).
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_complete_session()

        result = await orch.process_turn(session, "Yes that's all")

        assert result["action"] == "finalize"
        assert session.is_finalized is True
        assert mock_llm.call.call_count == 2  # phase1 + intake (finalize deferred)

    @pytest.mark.asyncio
    async def test_max_turns_triggers_finalize(self):
        """Reaching max turns should force finalization."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.3)
        finalize_output = _make_finalize_output()
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output, finalize_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session(max_turns=1)  # Already at limit after 1 turn

        result = await orch.process_turn(session, "mild headache for two days")

        assert result["action"] == "finalize"

    @pytest.mark.asyncio
    async def test_no_missing_fields_triggers_finalize(self):
        """If LLM reports no missing fields and SBAR fields are present, should finalize."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(
            missing_fields_prioritized=[],  # nothing missing
            confidence=0.6,  # below threshold but no missing fields
        )
        finalize_output = _make_finalize_output()
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output, finalize_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_complete_session()

        result = await orch.process_turn(session, "That's everything")

        assert result["action"] == "finalize"

    @pytest.mark.asyncio
    async def test_early_turn_blocks_finalize(self):
        """High confidence on turn 1 should NOT finalize — minimum turn gate."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.85)
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()  # turn_count=0

        result = await orch.process_turn(session, "I have a cough")

        assert result["action"] == "ask", "Should continue asking on early turns"
        assert session.is_finalized is False

    @pytest.mark.asyncio
    async def test_incomplete_sbar_blocks_finalize(self):
        """High confidence with missing SBAR fields should NOT finalize."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.85)
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        # Enough turns but missing severity and history
        session = _make_session()
        session.turn_count = 7
        session.intake_state.chief_complaint = "cough"
        session.intake_state.onset_time = "2 days ago"
        # symptom_severity still "unknown", lists still empty

        result = await orch.process_turn(session, "That's all")

        assert result["action"] == "ask", "Should keep asking when SBAR fields missing"


# -----------------------------------------------------------------------
# Test: LLM failure fallback
# -----------------------------------------------------------------------

class TestLLMFailure:
    @pytest.mark.asyncio
    async def test_llm_failure_uses_fallback(self):
        """LLM call error should produce a fallback question, not crash.

        NOTE (Phase 1): structured_call now runs for Phase1 safety analysis.
        If structured_call fails, Phase 1 fail-closed behaviour escalates
        instead of asking a fallback.
        This test verifies the fail-closed escalation path for structured_call errors.
        """
        mock_llm = MagicMock()
        # structured_call raises — triggers fail-closed escalation
        mock_llm.call = AsyncMock(side_effect=LLMCallError("API timeout"))

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "I have a headache")

        # Phase 1: fail-closed on LLM error → escalate (not just ask fallback)
        assert result["action"] == "escalate"
        assert "structured_call_failed" in result.get("fail_reason", "")
        assert len(result["message"]) > 0


# -----------------------------------------------------------------------
# Test: Deterministic override of LLM disposition
# -----------------------------------------------------------------------

class TestDeterministicOverride:
    @pytest.mark.asyncio
    async def test_override_llm_disposition_when_rules_triggered(self):
        """When deterministic rules triggered, finalize must override to ER_NOW."""
        mock_llm = MagicMock()
        finalize_output = _make_finalize_output(
            disposition=DispositionCategory.SCHEDULE,  # LLM says schedule
        )
        mock_llm.call = AsyncMock(return_value=finalize_output)

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        # Simulate deterministic rules having been triggered
        assert session.audit_trace is not None
        session.audit_trace.deterministic_rules_triggered.append("severe_breathing_difficulty")

        result = await orch.finalize(session)

        assert result.disposition == DispositionCategory.ER_NOW
        assert "Deterministic safety rules" in result.disposition_reasoning


# -----------------------------------------------------------------------
# Test: Audit trace
# -----------------------------------------------------------------------

class TestAuditTrace:
    @pytest.mark.asyncio
    async def test_audit_entries_recorded(self):
        """Each turn should produce audit trace entries."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.3)
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        await orch.process_turn(session, "I have a headache")

        assert session.audit_trace is not None
        assert len(session.audit_trace.entries) >= 2  # turn_start + intake_turn_complete
        assert session.audit_trace.entries[0].step == "turn_start"


# -----------------------------------------------------------------------
# Test: Coercion visibility
# -----------------------------------------------------------------------

class TestCoercionVisibility:
    @pytest.mark.asyncio
    async def test_coercion_tagged_when_string_flags(self):
        """When LLM returns string safety flags, coercion is recorded in session."""
        mock_llm = MagicMock()
        # Return an IntakeTurnOutput with string safety flags (will be coerced)
        intake_output = IntakeTurnOutput.model_validate({
            "next_question": "When did this start?",
            "llm_safety_flags": ["possible chest pain"],
            "confidence": 0.3,
            "missing_fields_prioritized": ["onset_time"],
        })
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        await orch.process_turn(session, "My chest hurts a little")

        # Coercion should be recorded
        assert "llm_safety_flags_coerced_from_strings" in session.llm_coercions

        # Audit trace entry should exist
        assert session.audit_trace is not None
        coercion_entries = [
            e for e in session.audit_trace.entries if e.step == "coercion"
        ]
        assert len(coercion_entries) == 1
        assert coercion_entries[0].agent == "llm_parse"
        assert coercion_entries[0].output_summary is not None
        assert "Coerced" in coercion_entries[0].output_summary

    @pytest.mark.asyncio
    async def test_no_coercion_when_proper_flags(self):
        """When LLM returns proper SafetyFlag objects, no coercion tag is set."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.3, llm_safety_flags=[])
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        await orch.process_turn(session, "just a mild headache")

        assert "llm_safety_flags_coerced_from_strings" not in session.llm_coercions

    def test_audit_trace_never_has_empty_session_id(self):
        """AuditTrace should always have a valid session_id, never empty or placeholder."""
        session = _make_session(session_id="real-id-123")
        assert session.audit_trace is not None
        assert session.audit_trace.session_id == "real-id-123"
        assert session.audit_trace.session_id != ""
        assert session.audit_trace.session_id != "_pending_"


# -----------------------------------------------------------------------
# Test: List-field merge semantics
# -----------------------------------------------------------------------

class TestListFieldMerge:
    @pytest.mark.asyncio
    async def test_list_merge_dedupes_case_insensitive(self):
        """List fields should append + case-insensitive dedupe across turns."""
        mock_llm = MagicMock()

        # Turn 1: LLM extracts meds=["Aspirin"]
        turn1_output = _make_intake_output(
            extracted_fields_update=IntakeStatePatch(meds=["Aspirin"]),
            confidence=0.3,
        )
        # Turn 2: LLM extracts meds=["Tylenol", "aspirin"] — "aspirin" is a dupe
        turn2_output = _make_intake_output(
            extracted_fields_update=IntakeStatePatch(meds=["Tylenol", "aspirin"]),
            confidence=0.4,
        )
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), turn1_output, _valid_phase1_result(), turn2_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        await orch.process_turn(session, "I take aspirin")
        await orch.process_turn(session, "Also tylenol")

        assert session.intake_state.meds == ["Aspirin", "Tylenol"]

    @pytest.mark.asyncio
    async def test_list_merge_caps_at_25(self):
        """Merged lists should be capped at MAX_LIST_ITEMS (25)."""
        mock_llm = MagicMock()

        # Provide 30 unique items in one shot
        big_list = [f"med_{i}" for i in range(30)]
        turn_output = _make_intake_output(
            extracted_fields_update=IntakeStatePatch(meds=big_list),
            confidence=0.3,
        )
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), turn_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        await orch.process_turn(session, "I take many medications")

        assert len(session.intake_state.meds) == 25

    @pytest.mark.asyncio
    async def test_notes_merge_and_cap(self):
        """Notes should concatenate with separator and cap at 500 chars."""
        mock_llm = MagicMock()

        turn1_output = _make_intake_output(
            extracted_fields_update=IntakeStatePatch(notes="First note"),
            confidence=0.3,
        )
        turn2_output = _make_intake_output(
            extracted_fields_update=IntakeStatePatch(notes="Second note"),
            confidence=0.4,
        )
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), turn1_output, _valid_phase1_result(), turn2_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        await orch.process_turn(session, "Some info")
        await orch.process_turn(session, "More info")

        assert session.intake_state.notes == "First note | Second note"
        assert len(session.intake_state.notes) <= 500
