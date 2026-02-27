"""
Regression tests for two production bugs:

BUG 1 — Double/compound reprompt on empty speech
    The TwiML <Gather> template previously included a <Say> fallback *outside*
    </Gather>, which played an apology before Twilio redirected back to the
    server.  The server-side empty-speech handler then produced a second
    apology + stage question, so callers heard two consecutive prompts.

    Fix:
    - generate_twiml_gather() no longer emits a <Say> after </Gather>.
    - The server-side handler uses exactly the single canonical sentence
      "Sorry, I didn't catch that. Can you please repeat your answer?" and does
      NOT append the stage question.

BUG 2 — Low confidence prematurely escalates / ends call with nurse transfer
    When the orchestrator computed confidence < 0.60 it always escalated, even
    when no red flags were present.

    Fix:
    - Low confidence + red flags still escalates (red-flag supremacy preserved).
    - Low confidence + NO red flags → continue intake with a deterministic
      follow-up question addressing the next missing SBAR field.  The decision
      trace includes low_confidence_reprompt=True, override_reason=
      "LOW_CONFIDENCE_CONTINUE_INTAKE".
"""

from __future__ import annotations

import json
import re
import pytest
import pytest_asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

# ──────────────────────────────────────────────────────────────────────────────
# BUG 1: TwiML / empty-speech handler
# ──────────────────────────────────────────────────────────────────────────────

from src.twilio.routes import generate_twiml_gather


class TestGenerateTwimlGather:
    """Unit tests for the TwiML Gather builder."""

    def test_gather_contains_prompt_inside_gather_tag(self):
        """The prompt must be spoken *inside* <Gather> so Twilio captures speech."""
        twiml = generate_twiml_gather("What is your name?", "/api/v1/voice/gather")
        assert "<Gather" in twiml
        assert "What is your name?" in twiml
        # The prompt must sit between <Gather …> and </Gather>
        gather_start = twiml.index("<Gather")
        gather_end = twiml.index("</Gather>") + len("</Gather>")
        inner = twiml[gather_start:gather_end]
        assert "What is your name?" in inner

    def test_no_fallback_say_outside_gather(self):
        """After the fix, there must be NO <Say> element outside </Gather>.

        This is the root cause of Bug 1: a <Say> outside the Gather plays an
        apology, then <Redirect> fires the server handler which produces a
        *second* apology + stage question.
        """
        twiml = generate_twiml_gather("What is your name?", "/api/v1/voice/gather")
        gather_end_pos = twiml.index("</Gather>") + len("</Gather>")
        after_gather = twiml[gather_end_pos:]
        # Must not contain a second <Say> after </Gather>
        assert "<Say" not in after_gather, (
            "Found a <Say> outside </Gather> — this causes the double-reprompt bug.\n"
            f"Trailing TwiML:\n{after_gather}"
        )

    def test_redirect_present_after_gather(self):
        """A <Redirect> must follow </Gather> so the server handles silence."""
        twiml = generate_twiml_gather("Test prompt.", "/api/v1/voice/gather")
        gather_end_pos = twiml.index("</Gather>") + len("</Gather>")
        after_gather = twiml[gather_end_pos:]
        assert "<Redirect" in after_gather

    def test_single_say_count_in_entire_twiml(self):
        """The complete TwiML must have exactly ONE <Say> element (the prompt)."""
        twiml = generate_twiml_gather("How old are you?", "/api/v1/voice/gather")
        say_count = twiml.count("<Say")
        assert say_count == 1, (
            f"Expected 1 <Say> in TwiML, found {say_count}.\n"
            "Multiple <Say> elements produce compound/double prompts."
        )

    def test_empty_speech_twiml_contains_only_one_apology(self):
        """Verify the canonical apology TwiML emitted for empty speech.

        The empty-speech handler calls generate_twiml_gather with the single
        canonical apology sentence.  The resulting TwiML must contain that
        sentence as text and must contain exactly one <Say>.
        """
        apology = "Sorry, I didn't catch that. Can you please repeat your answer?"
        twiml = generate_twiml_gather(apology, "/api/v1/voice/gather")

        assert apology in twiml
        # Only one <Say> — no stacked prompts
        assert twiml.count("<Say") == 1

    def test_empty_speech_twiml_no_stage_question_text(self):
        """The empty-speech TwiML must NOT contain stage question wording.

        Appending the stage question after the apology is the second half of
        Bug 1: callers would hear "Sorry … What is your name?" instead of just
        the single apology.
        """
        apology = "Sorry, I didn't catch that. Can you please repeat your answer?"
        twiml = generate_twiml_gather(apology, "/api/v1/voice/gather")

        forbidden_phrases = [
            "What is your full name",
            "What is your age",
            "biological sex",
            "main symptom",
            "Sorry, I didn't hear that",  # old compound wording
        ]
        for phrase in forbidden_phrases:
            assert phrase not in twiml, (
                f"TwiML for empty-speech reprompt contains forbidden phrase: {phrase!r}\n"
                "The empty-speech handler must not append stage questions."
            )


# ──────────────────────────────────────────────────────────────────────────────
# BUG 2: Low-confidence continue-intake vs escalation
# ──────────────────────────────────────────────────────────────────────────────

from src.orchestrator.orchestrator import Orchestrator, PHASE1_CONFIDENCE_ESCALATION_THRESHOLD
from src.orchestrator.schemas import (
    AuditTrace,
    OrchestratorSession,
    StructuredIntakeState,
    Phase1TurnOutput,
    Phase1Disposition,
    Phase1NextAction,
    IntakeTurnOutput,
    IntakeStatePatch,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_session(chief_complaint: str = "stomach pain") -> OrchestratorSession:
    """Minimal session that has passed scripted intake and is in DYNAMIC stage."""
    s = OrchestratorSession(session_id="test-bug2")
    s.intake_state.caller_name = "Test User"
    s.intake_state.caller_age = 30
    s.intake_state.caller_sex = "female"
    s.intake_state.chief_complaint = chief_complaint
    return s


def _low_confidence_phase1(red_flags: list[str] | None = None) -> Phase1TurnOutput:
    """Phase1TurnOutput that simulates confidence below threshold."""
    return Phase1TurnOutput(
        confidence_score=0.30,          # below 0.60 threshold
        escalation_required=False,
        red_flags_triggered=red_flags or [],
        rules_triggered=[],
        next_action=Phase1NextAction.ASK_QUESTION,
        disposition=Phase1Disposition.HUMAN_REVIEW,
    )


def _normal_intake_output(question: str = "How long have you had this pain?") -> IntakeTurnOutput:
    return IntakeTurnOutput(
        extracted_fields_update=IntakeStatePatch(),
        missing_fields_prioritized=["onset_time"],
        next_question=question,
        llm_safety_flags=[],
        confidence=0.3,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestLowConfidenceContinueIntake:
    """Bug 2: low confidence without red flags must NOT escalate."""

    # ------------------------------------------------------------------
    # Shared helper: build an orchestrator whose ConfidenceBreakdown
    # always finalises at the given score (bypassing the deterministic
    # deduction logic so the test controls confidence independently).
    # ------------------------------------------------------------------

    @staticmethod
    def _make_orch_with_forced_confidence(
        phase1_result: "Phase1TurnOutput",
        intake_result: "IntakeTurnOutput",
        forced_confidence: float = 0.40,
    ):
        """Return (orch, patch_target_string) with clamped confidence."""
        orch = Orchestrator.__new__(Orchestrator)
        mock_guarded = AsyncMock()
        mock_guarded.structured_call = AsyncMock(
            side_effect=[phase1_result, intake_result]
        )
        orch._guarded = mock_guarded
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        orch._retriever = mock_retriever
        return orch

    @pytest.mark.asyncio
    async def test_low_confidence_no_red_flags_returns_ask(self):
        """Chief complaint + low confidence + no red flags → action must be 'ask'."""
        session = _make_session()
        orch = self._make_orch_with_forced_confidence(
            _low_confidence_phase1(red_flags=[]),
            _normal_intake_output(),
        )
        with patch(
            "src.orchestrator.schemas.ConfidenceBreakdown.clamp_and_finalise",
            return_value=0.40,
        ):
            result = await orch.process_turn(session, "stomach pain")

        assert result["action"] == "ask", (
            f"Expected action='ask' for low confidence + no red flags, "
            f"got action={result['action']!r}.\n"
            "Low confidence alone must NOT trigger escalation."
        )

    @pytest.mark.asyncio
    async def test_low_confidence_no_red_flags_override_applied(self):
        """Decision trace must record the LOW_CONFIDENCE_CONTINUE_INTAKE override."""
        session = _make_session()
        orch = self._make_orch_with_forced_confidence(
            _low_confidence_phase1(red_flags=[]),
            _normal_intake_output(),
        )
        with patch(
            "src.orchestrator.schemas.ConfidenceBreakdown.clamp_and_finalise",
            return_value=0.40,
        ):
            result = await orch.process_turn(session, "stomach pain")

        assert result.get("override_applied") == "LOW_CONFIDENCE_CONTINUE_INTAKE", (
            "Result dict must carry override_applied='LOW_CONFIDENCE_CONTINUE_INTAKE'."
        )
        assert result.get("low_confidence_reprompt") is True
        assert result.get("intake_complete") is False

    @pytest.mark.asyncio
    async def test_low_confidence_no_red_flags_trace_fields(self):
        """Decision trace entry must have correct low-confidence fields."""
        session = _make_session()
        orch = self._make_orch_with_forced_confidence(
            _low_confidence_phase1(red_flags=[]),
            _normal_intake_output(),
        )
        with patch(
            "src.orchestrator.schemas.ConfidenceBreakdown.clamp_and_finalise",
            return_value=0.40,
        ):
            await orch.process_turn(session, "stomach pain")

        assert session.decision_trace, "Decision trace must have at least one entry."
        trace = session.decision_trace[-1]

        assert trace.low_confidence_reprompt is True
        assert trace.override_reason == "LOW_CONFIDENCE_CONTINUE_INTAKE"
        assert trace.escalation_required is False
        assert trace.disposition == "CONTINUE_INTAKE"

    @pytest.mark.asyncio
    async def test_low_confidence_no_red_flags_message_is_followup_question(self):
        """The spoken message must be a deterministic SBAR follow-up, not an apology."""
        session = _make_session()
        # onset_time is None → expect "When did your symptoms first start?"
        orch = self._make_orch_with_forced_confidence(
            _low_confidence_phase1(red_flags=[]),
            _normal_intake_output(),
        )
        with patch(
            "src.orchestrator.schemas.ConfidenceBreakdown.clamp_and_finalise",
            return_value=0.40,
        ):
            result = await orch.process_turn(session, "stomach pain")

        msg = result["message"]
        # Must not be the nurse-transfer escalation message
        assert "connecting you with a nurse" not in msg.lower(), (
            "Low confidence without red flags must NOT produce a nurse-transfer message."
        )
        # Must be a clinical follow-up question
        assert "?" in msg, "Follow-up message should be a question."

    @pytest.mark.asyncio
    async def test_low_confidence_no_red_flags_session_not_finalized(self):
        """Session must NOT be marked finalized when continuing intake."""
        session = _make_session()
        orch = self._make_orch_with_forced_confidence(
            _low_confidence_phase1(red_flags=[]),
            _normal_intake_output(),
        )
        with patch(
            "src.orchestrator.schemas.ConfidenceBreakdown.clamp_and_finalise",
            return_value=0.40,
        ):
            await orch.process_turn(session, "stomach pain")

        assert not session.is_finalized, (
            "Session must not be finalized when continuing intake due to low confidence."
        )


class TestLowConfidenceWithRedFlagsStillEscalates:
    """Bug 2: red-flag supremacy — low confidence + red flags must still escalate."""

    @pytest.mark.asyncio
    async def test_low_confidence_with_phase1_red_flags_escalates(self):
        """LLM-reported red flags + low confidence must still produce escalation."""
        session = _make_session(chief_complaint="chest pain radiating to arm")
        orch = Orchestrator.__new__(Orchestrator)

        mock_guarded = AsyncMock()
        mock_guarded.structured_call = AsyncMock(
            return_value=_low_confidence_phase1(red_flags=["chest_pain_radiation"])
        )
        orch._guarded = mock_guarded
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        orch._retriever = mock_retriever

        result = await orch.process_turn(session, "chest pain radiating to arm")

        assert result["action"] == "escalate", (
            "Red flags present → must escalate regardless of confidence score."
        )

    @pytest.mark.asyncio
    async def test_low_confidence_with_red_flags_trace_escalation_required(self):
        """Decision trace must show escalation_required=True when red flags present."""
        session = _make_session(chief_complaint="crushing chest pain")
        orch = Orchestrator.__new__(Orchestrator)

        mock_guarded = AsyncMock()
        mock_guarded.structured_call = AsyncMock(
            return_value=_low_confidence_phase1(red_flags=["chest_pain_severe"])
        )
        orch._guarded = mock_guarded
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        orch._retriever = mock_retriever

        await orch.process_turn(session, "crushing chest pain")

        assert session.decision_trace, "Decision trace must not be empty."
        trace = session.decision_trace[-1]
        assert trace.escalation_required is True
        assert trace.low_confidence_reprompt is False  # Was escalated, not reprompted

    @pytest.mark.asyncio
    async def test_low_confidence_with_red_flags_session_finalized(self):
        """Session must be finalized (call ended) when red flags cause escalation."""
        session = _make_session(chief_complaint="I can't breathe")
        orch = Orchestrator.__new__(Orchestrator)

        mock_guarded = AsyncMock()
        mock_guarded.structured_call = AsyncMock(
            return_value=_low_confidence_phase1(red_flags=["shortness_of_breath_severe"])
        )
        orch._guarded = mock_guarded
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        orch._retriever = mock_retriever

        result = await orch.process_turn(session, "I can't breathe")

        assert result["action"] == "escalate"
        assert session.is_finalized is True


class TestLowConfidenceContinueIntakeSBARProgression:
    """Verify the deterministic SBAR follow-up question ordering."""

    _CONFIDENCE_PATCH = "src.orchestrator.schemas.ConfidenceBreakdown.clamp_and_finalise"

    @pytest.mark.asyncio
    async def test_missing_onset_asked_first(self):
        """onset_time is None → first follow-up must ask about onset."""
        session = _make_session()
        # onset_time is None by default
        assert session.intake_state.onset_time is None

        orch = Orchestrator.__new__(Orchestrator)
        mock_guarded = AsyncMock()
        mock_guarded.structured_call = AsyncMock(
            side_effect=[
                _low_confidence_phase1(red_flags=[]),
                _normal_intake_output(),
            ]
        )
        orch._guarded = mock_guarded
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        orch._retriever = mock_retriever

        with patch(self._CONFIDENCE_PATCH, return_value=0.40):
            result = await orch.process_turn(session, "stomach pain")

        msg = result["message"].lower()
        # "When did your symptoms first start?" contains "start" and "when"
        assert "start" in msg or "when" in msg or "symptom" in msg, (
            f"When onset_time is missing, follow-up must ask about symptom onset. Got: {result['message']!r}"
        )

    @pytest.mark.asyncio
    async def test_severity_asked_when_onset_present(self):
        """onset_time filled → follow-up asks about severity."""
        session = _make_session()
        session.intake_state.onset_time = "yesterday morning"
        # symptom_severity is None

        orch = Orchestrator.__new__(Orchestrator)
        mock_guarded = AsyncMock()
        mock_guarded.structured_call = AsyncMock(
            side_effect=[
                _low_confidence_phase1(red_flags=[]),
                _normal_intake_output(),
            ]
        )
        orch._guarded = mock_guarded
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        orch._retriever = mock_retriever

        with patch(self._CONFIDENCE_PATCH, return_value=0.40):
            result = await orch.process_turn(session, "stomach pain")

        msg = result["message"].lower()
        assert "scale" in msg or "severe" in msg or "how" in msg, (
            "When onset_time is filled but severity is missing, "
            f"follow-up must ask about severity. Got: {result['message']!r}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# BUG 3 (regression): Low-confidence branch must apply extracted fields
# ──────────────────────────────────────────────────────────────────────────────

class TestLowConfidenceAppliesExtractedFields:
    """Regression: the low-confidence "continue intake" branch must resolve
    the concurrent intake LLM call and apply extracted_fields_update to
    session.intake_state *before* choosing the next follow-up question.

    Without this fix, the session state never updates and the orchestrator
    loops on the same missing SBAR slot forever.
    """

    _CONFIDENCE_PATCH = "src.orchestrator.schemas.ConfidenceBreakdown.clamp_and_finalise"

    @staticmethod
    def _make_session_missing_onset() -> OrchestratorSession:
        """Session where onset_time is the first missing SBAR slot."""
        s = OrchestratorSession(session_id="test-apply-fields")
        s.intake_state.caller_name = "Test User"
        s.intake_state.caller_age = 40
        s.intake_state.caller_sex = "male"
        s.intake_state.chief_complaint = "knee pain"
        # onset_time is None → first missing slot
        return s

    @staticmethod
    def _intake_with_onset(onset: str = "5 years") -> IntakeTurnOutput:
        """IntakeTurnOutput that extracts onset_time from the caller's answer."""
        return IntakeTurnOutput(
            extracted_fields_update=IntakeStatePatch(onset_time=onset),
            missing_fields_prioritized=["symptom_severity"],
            next_question="How severe is the pain right now?",
            llm_safety_flags=[],
            confidence=0.4,
        )

    @pytest.mark.asyncio
    async def test_extracted_onset_is_applied_in_low_confidence_branch(self):
        """After the low-confidence branch, onset_time must be set on the session."""
        session = self._make_session_missing_onset()
        assert session.intake_state.onset_time is None

        orch = Orchestrator.__new__(Orchestrator)
        mock_guarded = AsyncMock()
        mock_guarded.structured_call = AsyncMock(
            side_effect=[
                _low_confidence_phase1(red_flags=[]),
                self._intake_with_onset("5 years"),
            ]
        )
        orch._guarded = mock_guarded
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        orch._retriever = mock_retriever

        with patch(self._CONFIDENCE_PATCH, return_value=0.40):
            result = await orch.process_turn(session, "for five years")

        # The branch must still return "ask" (not escalate)
        assert result["action"] == "ask"

        # CRITICAL ASSERTION: onset_time must now be set
        assert session.intake_state.onset_time == "5 years", (
            f"Expected onset_time='5 years' after low-confidence branch applied "
            f"extracted fields, but got {session.intake_state.onset_time!r}. "
            "This is the root cause of the infinite loop bug."
        )

    @pytest.mark.asyncio
    async def test_followup_advances_to_next_slot_after_extraction(self):
        """After onset_time is extracted, the follow-up must NOT ask about
        onset again — it should advance to the next missing slot (severity).
        """
        session = self._make_session_missing_onset()
        assert session.intake_state.onset_time is None
        assert session.intake_state.symptom_severity is None  # next slot

        orch = Orchestrator.__new__(Orchestrator)
        mock_guarded = AsyncMock()
        mock_guarded.structured_call = AsyncMock(
            side_effect=[
                _low_confidence_phase1(red_flags=[]),
                self._intake_with_onset("5 years"),
            ]
        )
        orch._guarded = mock_guarded
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        orch._retriever = mock_retriever

        with patch(self._CONFIDENCE_PATCH, return_value=0.40):
            result = await orch.process_turn(session, "for five years")

        msg = result["message"].lower()
        # Must NOT be asking about onset/start again
        assert "start" not in msg and "when did" not in msg, (
            f"After onset_time is extracted, follow-up must NOT ask about onset "
            f"again. Got: {result['message']!r}"
        )
        # Should be asking about severity (the next missing SBAR field)
        assert "scale" in msg or "severe" in msg or "discomfort" in msg, (
            f"Follow-up should advance to severity question. Got: {result['message']!r}"
        )

    @pytest.mark.asyncio
    async def test_intake_output_returned_in_low_confidence_branch(self):
        """The result dict should include intake_output for downstream use."""
        session = self._make_session_missing_onset()
        orch = Orchestrator.__new__(Orchestrator)
        mock_guarded = AsyncMock()
        mock_guarded.structured_call = AsyncMock(
            side_effect=[
                _low_confidence_phase1(red_flags=[]),
                self._intake_with_onset("2 days ago"),
            ]
        )
        orch._guarded = mock_guarded
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        orch._retriever = mock_retriever

        with patch(self._CONFIDENCE_PATCH, return_value=0.40):
            result = await orch.process_turn(session, "2 days ago")

        assert "intake_output" in result, (
            "Result dict must include intake_output in low-confidence branch."
        )

    @pytest.mark.asyncio
    async def test_intake_exception_uses_fallback(self):
        """If the intake LLM call fails, fallback must be used and state
        must not crash (fields simply won't be updated).
        """
        session = self._make_session_missing_onset()
        orch = Orchestrator.__new__(Orchestrator)
        mock_guarded = AsyncMock()
        # Phase1 succeeds, intake raises
        mock_guarded.structured_call = AsyncMock(
            side_effect=[
                _low_confidence_phase1(red_flags=[]),
                RuntimeError("LLM timeout"),
            ]
        )
        orch._guarded = mock_guarded
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        orch._retriever = mock_retriever

        with patch(self._CONFIDENCE_PATCH, return_value=0.40):
            result = await orch.process_turn(session, "for five years")

        # Must still return ask, not crash
        assert result["action"] == "ask"
        # onset_time won't be filled because intake failed, but no crash
        assert result.get("low_confidence_reprompt") is True

    @pytest.mark.asyncio
    async def test_multi_turn_does_not_loop_on_same_slot(self):
        """Simulate 2 turns: first fills onset, second should NOT re-ask onset."""
        session = self._make_session_missing_onset()

        # Turn 1: caller says onset, intake extracts it
        orch1 = Orchestrator.__new__(Orchestrator)
        mock_guarded1 = AsyncMock()
        mock_guarded1.structured_call = AsyncMock(
            side_effect=[
                _low_confidence_phase1(red_flags=[]),
                self._intake_with_onset("5 years"),
            ]
        )
        orch1._guarded = mock_guarded1
        mock_retriever1 = MagicMock()
        mock_retriever1.retrieve.return_value = []
        orch1._retriever = mock_retriever1

        with patch(self._CONFIDENCE_PATCH, return_value=0.40):
            result1 = await orch1.process_turn(session, "for five years")

        assert result1["action"] == "ask"
        assert session.intake_state.onset_time == "5 years"

        # Turn 2: caller answers severity, intake extracts it
        orch2 = Orchestrator.__new__(Orchestrator)
        mock_guarded2 = AsyncMock()
        severity_intake = IntakeTurnOutput(
            extracted_fields_update=IntakeStatePatch(symptom_severity="moderate"),
            missing_fields_prioritized=["relevant_history"],
            next_question="Do you have any relevant medical history?",
            llm_safety_flags=[],
            confidence=0.45,
        )
        mock_guarded2.structured_call = AsyncMock(
            side_effect=[
                _low_confidence_phase1(red_flags=[]),
                severity_intake,
            ]
        )
        orch2._guarded = mock_guarded2
        mock_retriever2 = MagicMock()
        mock_retriever2.retrieve.return_value = []
        orch2._retriever = mock_retriever2

        with patch(self._CONFIDENCE_PATCH, return_value=0.40):
            result2 = await orch2.process_turn(session, "about a 5 out of 10")

        assert result2["action"] == "ask"
        assert session.intake_state.symptom_severity == "moderate"
        # Follow-up should now be about relevant_history, not onset or severity
        msg2 = result2["message"].lower()
        assert "start" not in msg2, (
            f"Turn 2 must not re-ask onset. Got: {result2['message']!r}"
        )


class TestLowConfidenceLoopBreaker:
    """Loop-breaker: if the same slot is asked 3+ times, switch to
    forced-choice wording (but do NOT escalate).
    """

    _CONFIDENCE_PATCH = "src.orchestrator.schemas.ConfidenceBreakdown.clamp_and_finalise"

    @staticmethod
    def _intake_no_extraction() -> IntakeTurnOutput:
        """IntakeTurnOutput that extracts nothing (simulates failed parse)."""
        return IntakeTurnOutput(
            extracted_fields_update=IntakeStatePatch(),
            missing_fields_prioritized=["onset_time"],
            next_question="When did your symptoms start?",
            llm_safety_flags=[],
            confidence=0.3,
        )

    @pytest.mark.asyncio
    async def test_loop_breaker_activates_after_repeated_slot(self):
        """After asking the same slot 3 times, wording should change."""
        session = _make_session()  # onset_time is None

        for i in range(3):
            orch = Orchestrator.__new__(Orchestrator)
            mock_guarded = AsyncMock()
            mock_guarded.structured_call = AsyncMock(
                side_effect=[
                    _low_confidence_phase1(red_flags=[]),
                    self._intake_no_extraction(),
                ]
            )
            orch._guarded = mock_guarded
            mock_retriever = MagicMock()
            mock_retriever.retrieve.return_value = []
            orch._retriever = mock_retriever

            with patch(self._CONFIDENCE_PATCH, return_value=0.40):
                result = await orch.process_turn(session, "I don't know exactly")

            assert result["action"] == "ask", (
                f"Turn {i+1}: must still be 'ask', not escalate."
            )

        # After 3 repeats, the wording should be the forced-choice variant
        msg = result["message"]
        assert "would you say" in msg.lower() or "began" in msg.lower(), (
            f"After 3 repeats of onset_time, loop-breaker wording expected. "
            f"Got: {msg!r}"
        )
        # Must NOT escalate
        assert result["action"] == "ask"
        assert not session.is_finalized

    @pytest.mark.asyncio
    async def test_loop_breaker_counter_resets_on_slot_change(self):
        """If the slot changes between turns, repeat counter resets."""
        session = _make_session()

        # Turn 1: onset asked
        orch1 = Orchestrator.__new__(Orchestrator)
        mock_guarded1 = AsyncMock()
        mock_guarded1.structured_call = AsyncMock(
            side_effect=[
                _low_confidence_phase1(red_flags=[]),
                self._intake_no_extraction(),
            ]
        )
        orch1._guarded = mock_guarded1
        mock_retriever1 = MagicMock()
        mock_retriever1.retrieve.return_value = []
        orch1._retriever = mock_retriever1

        with patch(self._CONFIDENCE_PATCH, return_value=0.40):
            await orch1.process_turn(session, "hmm...")

        assert session.followup_repeat_count == 1
        assert session.last_followup_slot == "onset_time"

        # Turn 2: fill onset, now severity is the slot
        orch2 = Orchestrator.__new__(Orchestrator)
        mock_guarded2 = AsyncMock()
        intake_with_onset = IntakeTurnOutput(
            extracted_fields_update=IntakeStatePatch(onset_time="2 weeks"),
            missing_fields_prioritized=["symptom_severity"],
            next_question="How severe?",
            llm_safety_flags=[],
            confidence=0.4,
        )
        mock_guarded2.structured_call = AsyncMock(
            side_effect=[
                _low_confidence_phase1(red_flags=[]),
                intake_with_onset,
            ]
        )
        orch2._guarded = mock_guarded2
        mock_retriever2 = MagicMock()
        mock_retriever2.retrieve.return_value = []
        orch2._retriever = mock_retriever2

        with patch(self._CONFIDENCE_PATCH, return_value=0.40):
            await orch2.process_turn(session, "about two weeks")

        # Slot changed → counter should reset
        assert session.last_followup_slot == "symptom_severity"
        assert session.followup_repeat_count == 1
