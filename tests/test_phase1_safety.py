"""
Phase 1 Clinical Core — Safety Acceptance Tests

Covers every acceptance criterion from the Phase 1 spec:

  A) Pre-check safety gate (CRITICAL → ER_NOW, score≥10 → URGENT)
  B) Red-flag scoring model (score_red_flags)
  C) Confidence scoring (deductions + clamp)
  D) Confused-caller protocol (retry once → escalate)
  E) JSON validation + repair (2-attempt; fail-closed on double failure)
  F) Fail-closed conditions:
       - LLM timeout → escalate
       - JSON invalid twice → escalate
       - confidence < 0.60 → escalate
       - red-flag logic exception → escalate
       - post-check safety violation → escalate
  G) Decision trace logging (DecisionTraceEntry stored per turn)
  H) Post-check safety gate (diagnoses / unsafe instructions / urgency downgrade)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.schemas import (
    ConfidenceBreakdown,
    DecisionTraceEntry,
    FinalizeOutput,
    IntakeTurnOutput,
    OrchestratorSession,
    Phase1Disposition,
    Phase1NextAction,
    Phase1TurnOutput,
)
from src.safety.red_flags import score_red_flags
from src.orchestrator.validators import (
    post_check_safety_gate,
    PostCheckViolation,
    safe_phase1_escalation,
    validate_phase1_output,
)
from src.llm.client import LLMCallError


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _make_session(**kwargs) -> OrchestratorSession:
    defaults = {
        "session_id": "phase1-test-001",
        "max_turns": 12,
        "confidence_threshold": 0.75,
    }
    defaults.update(kwargs)
    return OrchestratorSession(**defaults)


def _make_finalize_output(**kwargs) -> FinalizeOutput:
    defaults = {
        "disposition": "ROUTINE",
        "disposition_reasoning": "Mild symptoms, no red flags",
        "safety_net_instructions": "Go to ER if symptoms worsen",
        "sbar_report": "S: Test\nB: Test\nA: Test\nR: Test",
        "patient_summary": "A nurse will contact you soon.",
        "llm_safety_flags": [],
    }
    defaults.update(kwargs)
    return FinalizeOutput(**defaults)


def _valid_phase1_json() -> str:
    """Return a minimal valid Phase1TurnOutput JSON string."""
    import json
    return json.dumps({
        "confidence_score": 0.8,
        "escalation_required": False,
        "red_flags_triggered": [],
        "rules_triggered": [],
        "next_action": "ASK_QUESTION",
        "disposition": "UNDECIDED",
    })


def _make_mock_llm_raw(raw: str):
    """Build a mock LLM that returns a given raw string from _raw_call."""
    mock_llm = MagicMock()
    mock_llm._raw_call = AsyncMock(return_value=raw)
    mock_llm.call = AsyncMock()
    return mock_llm


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION B — Red Flag Scoring Model
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRedFlagScoringModel:
    """score_red_flags() must return (disposition, score, ids) correctly."""

    def test_critical_flag_returns_er_now(self):
        disp, score, ids = score_red_flags(utterance="I can't breathe at all")
        assert disp == "ER_NOW"
        assert "rf_severe_breathing_failure" in ids

    def test_critical_flag_cardiac_returns_er_now(self):
        disp, score, ids = score_red_flags(utterance="I have crushing chest pain radiating to my arm")
        assert disp == "ER_NOW"
        assert "rf_cardiac_arrest_signs" in ids

    def test_critical_flag_stroke_returns_er_now(self):
        disp, score, ids = score_red_flags(utterance="I think I'm having a stroke")
        assert disp == "ER_NOW"
        assert "rf_stroke_signs" in ids

    def test_critical_flag_suicidal_returns_er_now(self):
        disp, score, ids = score_red_flags(utterance="I want to end my life")
        assert disp == "ER_NOW"
        assert "rf_suicidal_self_harm" in ids

    def test_critical_flag_loss_of_consciousness_returns_er_now(self):
        disp, score, ids = score_red_flags(utterance="My husband collapsed and won't wake up")
        assert disp == "ER_NOW"
        assert "rf_loss_of_consciousness" in ids

    def test_weighted_flags_score_ge_10_returns_urgent(self):
        """Two high-weight weighted flags alone should reach score ≥ 10."""
        # rf_high_fever(5) + rf_altered_mental_status(6) = 11
        disp, score, ids = score_red_flags(
            utterance="She has a very high fever and seems confused and disoriented"
        )
        assert disp == "URGENT"
        assert score >= 10
        assert "rf_high_fever" in ids
        assert "rf_altered_mental_status" in ids

    def test_weighted_flags_low_score_returns_undecided(self):
        """Single low-weight flag below threshold should return UNDECIDED."""
        # rf_worsening_symptoms = 4 points
        disp, score, ids = score_red_flags(
            utterance="My symptoms are getting worse over the past hour"
        )
        # 4 < 10, so UNDECIDED
        assert disp == "UNDECIDED"
        assert score == 4

    def test_no_flags_returns_undecided_zero(self):
        disp, score, ids = score_red_flags(utterance="I have a mild cold")
        assert disp == "UNDECIDED"
        assert score == 0
        assert ids == []

    def test_critical_overrides_score(self):
        """Even if weighted score < 10, a critical flag must return ER_NOW."""
        # A critical flag combined with a low score
        disp, score, ids = score_red_flags(
            utterance="I passed out and I am a little tired"
        )
        assert disp == "ER_NOW"

    def test_state_fields_scored_via_chief_complaint(self):
        """chief_complaint containing critical text → ER_NOW."""
        disp, score, ids = score_red_flags(
            utterance="",
            chief_complaint="seizure after head trauma",
        )
        assert disp == "ER_NOW"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION A — Pre-Check Safety Gate (via orchestrator)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestPreCheckSafetyGate:
    """Pre-check gate must escalate before any LLM call."""

    @pytest.mark.asyncio
    async def test_critical_flag_escalates_no_llm(self):
        """Critical red flag → ER_NOW escalation, LLM never called."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "I can't breathe at all")

        assert result["action"] == "escalate"
        assert session.is_finalized is True
        # LLM must NOT have been called
        mock_llm._raw_call.assert_not_called()
        mock_llm.call.assert_not_called()
        # Decision trace logged
        assert len(session.decision_trace) == 1
        assert session.decision_trace[0].escalation_required is True
        assert session.decision_trace[0].disposition == "ER_NOW"

    @pytest.mark.asyncio
    async def test_urgent_flag_score_escalates_no_llm(self):
        """Weighted score ≥ 10 → URGENT escalation, LLM never called."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        # Triggers rf_high_fever(5) + rf_altered_mental_status(6) = 11
        result = await orch.process_turn(
            session, "She has a very high fever now and is confused and disoriented"
        )

        assert result["action"] == "escalate"
        assert session.is_finalized is True
        mock_llm._raw_call.assert_not_called()
        mock_llm.call.assert_not_called()
        assert session.decision_trace[0].disposition == "URGENT"

    @pytest.mark.asyncio
    async def test_red_flag_exception_fails_closed(self):
        """If score_red_flags raises, must fail closed (escalate)."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        with patch(
            "src.orchestrator.orchestrator.score_red_flags",
            side_effect=RuntimeError("scoring engine error"),
        ):
            result = await orch.process_turn(session, "I have some chest tightness")

        assert result["action"] == "escalate"
        assert "red_flag_exception" in result.get("fail_reason", "")
        mock_llm._raw_call.assert_not_called()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION D — Confused Caller Protocol
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestConfusedCallerProtocol:
    """Unclear answer → retry ladder → escalate on third unclear."""

    @pytest.mark.asyncio
    async def test_first_unclear_asks_clarification(self):
        """First unclear answer → retry ladder step 1 (no LLM call)."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "")  # empty = unclear

        assert result["action"] == "ask"
        assert "didn't quite catch" in result["message"].lower() or "rephrase" in result["message"].lower()
        assert session.unclear_answer_retries == 1
        mock_llm._raw_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_third_unclear_escalates(self):
        """Three unclear answers → escalate to human (retry ladder)."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        # First unclear
        result1 = await orch.process_turn(session, "")
        assert result1["action"] == "ask"
        assert session.unclear_answer_retries == 1

        # Second unclear
        result2 = await orch.process_turn(session, "hmmm")
        assert result2["action"] == "ask"
        assert session.unclear_answer_retries == 2

        # Third unclear → escalate
        result3 = await orch.process_turn(session, "uh")
        assert result3["action"] == "escalate"
        assert result3.get("fail_reason") == "confused_caller_max_retries"
        assert session.is_finalized is True
        mock_llm._raw_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_i_dont_know_is_unclear(self):
        """'I don't know' equivalents should be classified as unclear."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "I don't know")
        assert result["action"] == "ask"  # first retry
        assert session.unclear_answer_retries == 1

    @pytest.mark.asyncio
    async def test_clear_answer_resets_retry_counter(self):
        """A clear answer after an unclear one must reset the retry counter."""
        mock_llm = MagicMock()
        # Phase1 raw call returns valid JSON; intake call returns IntakeTurnOutput
        intake_out = IntakeTurnOutput(
            next_question="How long has this been going on?",
            confidence=0.4,
            missing_fields_prioritized=["onset_time"],
        )
        mock_llm._raw_call = AsyncMock(return_value=_valid_phase1_json())
        mock_llm.call = AsyncMock(return_value=intake_out)

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        # First turn: unclear
        await orch.process_turn(session, "")
        assert session.unclear_answer_retries == 1

        # Second turn: clear answer
        await orch.process_turn(session, "I have had a headache for two days")
        assert session.unclear_answer_retries == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION E — JSON Validation + Repair
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestJsonValidationAndRepair:
    """validate_phase1_output: attempt 1 → repair → fail-closed."""

    @pytest.mark.asyncio
    async def test_valid_json_passes_first_attempt(self):
        raw = _valid_phase1_json()
        calls = []

        async def _repair(bad_raw, err):
            calls.append(bad_raw)
            return bad_raw

        obj, repaired = await validate_phase1_output(raw, _repair)
        assert obj is not None
        assert repaired is False
        assert len(calls) == 0  # repair was not called

    @pytest.mark.asyncio
    async def test_invalid_then_valid_uses_repair(self):
        invalid_raw = '{"confidence_score": "not_a_number"}'
        valid_raw = _valid_phase1_json()

        async def _repair(bad_raw, err):
            return valid_raw

        obj, repaired = await validate_phase1_output(invalid_raw, _repair)
        assert obj is not None
        assert repaired is True

    @pytest.mark.asyncio
    async def test_invalid_twice_returns_none(self):
        invalid_raw = "this is not json"

        async def _repair(bad_raw, err):
            return "still not json"

        obj, repaired = await validate_phase1_output(invalid_raw, _repair)
        assert obj is None

    @pytest.mark.asyncio
    async def test_json_invalid_twice_in_orchestrator_escalates(self):
        """Double JSON failure in full orchestrator call → fail-closed escalation.

        In the converged architecture, structured_call handles validation
        internally. Double JSON failure surfaces as LLMCallError.
        """
        mock_llm = MagicMock()
        mock_llm.call = AsyncMock(
            side_effect=LLMCallError("JSON validation failed after repair attempt")
        )

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "I have a headache")

        assert result["action"] == "escalate"
        assert "structured_call_failed" in result.get("fail_reason", "")
        assert session.is_finalized is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION F — Fail-Closed Conditions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFailClosedConditions:
    """All fail-closed paths must result in action=escalate."""

    @pytest.mark.asyncio
    async def test_llm_timeout_fails_closed(self):
        """LLM call raising generic Exception → fail-closed escalation."""
        mock_llm = MagicMock()
        mock_llm.call = AsyncMock(side_effect=Exception("Connection timeout"))

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "I have a moderate headache")

        assert result["action"] == "escalate"
        assert "llm_timeout" in result.get("fail_reason", "")
        assert session.is_finalized is True

    @pytest.mark.asyncio
    async def test_low_confidence_escalates(self):
        """confidence < 0.60 → escalate.

        Strategy: missing key info (-0.15) + contradiction (-0.20)
        + ambiguous weighted flags (-0.30) = final confidence 0.35 < 0.60.

        Utterance triggers rf_worsening_symptoms (weight=4, score<10)
        so the ambiguous_weighted_flags deduction fires.
        """
        phase1_result = Phase1TurnOutput(
            confidence_score=0.9,
            escalation_required=False,
            red_flags_triggered=["possible_chest_pain"],
            rules_triggered=[],
            next_action=Phase1NextAction.ASK_QUESTION,
            disposition=Phase1Disposition.HUMAN_REVIEW,
        )

        mock_llm = MagicMock()
        mock_llm.call = AsyncMock(return_value=phase1_result)

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()
        # Missing age / chief_complaint / onset_time → -0.15
        # symptom_severity = mild + red_flags present → contradiction -0.20
        session.intake_state.symptom_severity = "mild"

        # "getting worse" triggers rf_worsening_symptoms (weight=4)
        # → ambiguous_weighted_flags deduction -0.30
        result = await orch.process_turn(session, "mild discomfort getting worse")

        # Deductions: -0.15 (missing) + -0.20 (contradiction) + -0.30 (ambiguous) = -0.65
        # Final confidence = 0.35 < 0.60 → must escalate
        assert result["action"] == "escalate"
        assert "low_confidence" in result.get("fail_reason", "")

    @pytest.mark.asyncio
    async def test_low_confidence_definitive_escalates(self):
        """Definitive test: confidence deliberately computed below 0.60 → escalate.

        Deductions: missing_key_info(-0.15) + contradiction(-0.20)
        + ambiguous_weighted_flags(-0.30) = -0.65 → 0.35 < 0.60.
        """
        phase1_result = Phase1TurnOutput(
            confidence_score=0.5,
            escalation_required=False,
            red_flags_triggered=["possible chest pain"],
            rules_triggered=[],
            next_action=Phase1NextAction.ASK_QUESTION,
            disposition=Phase1Disposition.HUMAN_REVIEW,
        )

        mock_llm = MagicMock()
        mock_llm.call = AsyncMock(return_value=phase1_result)

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()
        # Set severity to mild so contradiction applies (-0.20)
        session.intake_state.symptom_severity = "mild"
        # No chief_complaint/age/onset_time → missing key info deduction (-0.15)

        # "getting worse" triggers rf_worsening_symptoms (weight=4, score<10)
        # → ambiguous_weighted_flags deduction (-0.30)
        result = await orch.process_turn(session, "mild discomfort getting worse")

        # Deductions: missing(-0.15) + contradiction(-0.20) + ambiguous(-0.30) = -0.65
        # Final confidence = 0.35 < 0.60 → escalate
        assert result["action"] == "escalate"
        assert "low_confidence" in result.get("fail_reason", "")

    @pytest.mark.asyncio
    async def test_post_check_violation_escalates(self):
        """Post-check detecting urgency downgrade → escalate.

        The urgency-downgrade check in post_check_safety_gate detects when
        the LLM tries to lower urgency (ER_NOW → SCHEDULE).
        """
        # SCHEDULE disposition ("ROUTINE" normalizes to SCHEDULE in schema)
        routine_output = Phase1TurnOutput(
            confidence_score=0.9,
            escalation_required=False,
            red_flags_triggered=[],
            rules_triggered=[],
            next_action=Phase1NextAction.ASK_QUESTION,
            disposition=Phase1Disposition.SCHEDULE,
        )

        mock_llm = MagicMock()
        mock_llm.call = AsyncMock(return_value=routine_output)

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()
        session.intake_state.caller_age = 35
        session.intake_state.chief_complaint = "chest pain"
        session.intake_state.onset_time = "2 hours"

        # Inject a prior turn with ER_NOW so SCHEDULE is a downgrade
        session.decision_trace.append(DecisionTraceEntry(
            turn_number=0,
            user_text="My chest hurts badly",
            confidence_score=0.9,
            disposition="ER_NOW",
            escalation_required=False,
            system_response="Please stay on the line.",
        ))

        result = await orch.process_turn(session, "I feel much better now")

        assert result["action"] == "escalate"
        assert "post_check_violation" in result.get("fail_reason", "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION C — Confidence Scoring (unit tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestConfidenceScoring:
    """Deterministic confidence deductions are calculated correctly."""

    def test_full_confidence_no_deductions(self):
        bd = ConfidenceBreakdown()
        score = bd.clamp_and_finalise()
        assert score == 1.0

    def test_missing_key_info_deduction(self):
        bd = ConfidenceBreakdown()
        bd.apply("missing_key_info", 0.15)
        score = bd.clamp_and_finalise()
        assert abs(score - 0.85) < 0.001

    def test_multiple_deductions_sum(self):
        bd = ConfidenceBreakdown()
        bd.apply("missing_key_info", 0.15)
        bd.apply("contradiction", 0.20)
        bd.apply("unclear_answer", 0.15)
        bd.apply("llm_repair", 0.20)
        score = bd.clamp_and_finalise()
        assert abs(score - 0.30) < 0.001

    def test_clamp_not_below_zero(self):
        bd = ConfidenceBreakdown()
        bd.apply("reason1", 0.60)
        bd.apply("reason2", 0.60)
        score = bd.clamp_and_finalise()
        assert score == 0.0

    def test_clamp_not_above_one(self):
        bd = ConfidenceBreakdown()
        # No deductions — default stays at 1.0
        score = bd.clamp_and_finalise()
        assert score == 1.0

    def test_confidence_below_threshold_flag(self):
        """Verify 0.60 threshold logic explicitly."""
        from src.orchestrator.orchestrator import PHASE1_CONFIDENCE_ESCALATION_THRESHOLD
        assert PHASE1_CONFIDENCE_ESCALATION_THRESHOLD == 0.60

        bd = ConfidenceBreakdown()
        bd.apply("large_deduction", 0.50)
        score = bd.clamp_and_finalise()
        assert score < PHASE1_CONFIDENCE_ESCALATION_THRESHOLD


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION H — Post-Check Safety Gate (unit tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestPostCheckSafetyGate:
    """post_check_safety_gate must raise PostCheckViolation on violations."""

    def _make_p1_output(self, disposition: str = "UNDECIDED") -> Phase1TurnOutput:
        return Phase1TurnOutput.model_validate({
            "confidence_score": 0.7,
            "escalation_required": False,
            "red_flags_triggered": [],
            "rules_triggered": [],
            "next_action": "ASK_QUESTION",
            "disposition": disposition,
        })

    def test_clean_output_passes(self):
        """No violation in normal output."""
        out = self._make_p1_output()
        post_check_safety_gate(
            llm_response_text='{"confidence_score": 0.7, "disposition": "UNDECIDED"}',
            previous_disposition=None,
            phase1_output=out,
        )  # Should not raise

    def test_diagnosis_statement_raises(self):
        with pytest.raises(PostCheckViolation) as exc_info:
            post_check_safety_gate(
                llm_response_text="This is likely a case of pneumonia.",
                previous_disposition=None,
                phase1_output=None,
            )
        assert "diagnosis" in exc_info.value.reason.lower()

    def test_unsafe_instruction_raises(self):
        with pytest.raises(PostCheckViolation) as exc_info:
            post_check_safety_gate(
                llm_response_text="You don't need to call 911 for this.",
                previous_disposition=None,
                phase1_output=None,
            )
        assert "unsafe" in exc_info.value.reason.lower()

    def test_urgency_downgrade_raises(self):
        """LLM attempting to lower urgency from URGENT → ROUTINE raises."""
        out = self._make_p1_output(disposition="ROUTINE")
        with pytest.raises(PostCheckViolation) as exc_info:
            post_check_safety_gate(
                llm_response_text='{"disposition": "ROUTINE"}',
                previous_disposition="URGENT",
                phase1_output=out,
            )
        assert "downgrade" in exc_info.value.reason.lower()

    def test_same_urgency_does_not_raise(self):
        """Same-level disposition is fine."""
        out = self._make_p1_output(disposition="URGENT")
        post_check_safety_gate(
            llm_response_text='{"disposition": "URGENT"}',
            previous_disposition="URGENT",
            phase1_output=out,
        )  # Should not raise

    def test_upgrade_does_not_raise(self):
        """Upgrading urgency (ROUTINE → ER_NOW) is acceptable."""
        out = self._make_p1_output(disposition="ER_NOW")
        post_check_safety_gate(
            llm_response_text='{"disposition": "ER_NOW"}',
            previous_disposition="ROUTINE",
            phase1_output=out,
        )  # Should not raise


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION G — Decision Trace Logging
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDecisionTraceLogging:
    """DecisionTraceEntry must be appended to session.decision_trace each turn."""

    @pytest.mark.asyncio
    async def test_trace_entry_appended_on_escalation(self):
        """Trace entry must exist even when escalating (pre-check ER_NOW)."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        await orch.process_turn(session, "I can't breathe and I'm turning blue")

        assert len(session.decision_trace) == 1
        entry = session.decision_trace[0]
        assert entry.turn_number == 1
        assert "I can't breathe" in entry.user_text
        assert entry.escalation_required is True
        assert entry.disposition == "ER_NOW"

    @pytest.mark.asyncio
    async def test_trace_entry_contains_required_fields(self):
        """DecisionTraceEntry must have all Phase 1 required fields."""
        mock_llm = MagicMock()
        intake_out = IntakeTurnOutput(
            next_question="How long has this been going on?",
            confidence=0.4,
            missing_fields_prioritized=["onset_time"],
        )
        mock_llm._raw_call = AsyncMock(return_value=_valid_phase1_json())
        mock_llm.call = AsyncMock(return_value=intake_out)

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()
        session.intake_state.caller_age = 45
        session.intake_state.chief_complaint = "headache"
        session.intake_state.onset_time = "2 days"

        await orch.process_turn(session, "I have had a headache for two days")

        assert len(session.decision_trace) == 1
        entry = session.decision_trace[0]

        # All required Phase 1 fields
        assert hasattr(entry, "timestamp")
        assert hasattr(entry, "user_text")
        assert hasattr(entry, "extracted_entities")
        assert hasattr(entry, "red_flags_triggered")
        assert hasattr(entry, "rules_triggered")
        assert hasattr(entry, "confidence_score")
        assert hasattr(entry, "disposition")
        assert hasattr(entry, "escalation_required")
        assert hasattr(entry, "system_response")

        assert isinstance(entry.red_flags_triggered, list)
        assert isinstance(entry.rules_triggered, list)
        assert 0.0 <= entry.confidence_score <= 1.0

    @pytest.mark.asyncio
    async def test_multiple_turns_accumulate_trace(self):
        """Each turn appends a new entry."""
        mock_llm = MagicMock()
        intake_out = IntakeTurnOutput(
            next_question="Can you tell me more?",
            confidence=0.4,
            missing_fields_prioritized=["onset_time"],
        )
        mock_llm._raw_call = AsyncMock(return_value=_valid_phase1_json())
        mock_llm.call = AsyncMock(return_value=intake_out)

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()
        session.intake_state.caller_age = 30
        session.intake_state.chief_complaint = "headache"
        session.intake_state.onset_time = "2 days"

        await orch.process_turn(session, "I have had a mild headache for two days")
        await orch.process_turn(session, "It started gradually")

        assert len(session.decision_trace) == 2
        assert session.decision_trace[0].turn_number == 1
        assert session.decision_trace[1].turn_number == 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION — Schema validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestPhase1OutputSchema:
    """Phase1TurnOutput schema validation."""

    def test_valid_schema_parses(self):
        import json as _json
        data = _json.loads(_valid_phase1_json())
        obj = Phase1TurnOutput.model_validate(data)
        assert obj.confidence_score == 0.8
        assert obj.next_action == Phase1NextAction.ASK_QUESTION
        assert obj.disposition == Phase1Disposition.HUMAN_REVIEW

    def test_missing_required_field_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Phase1TurnOutput.model_validate({
                "confidence_score": 0.5,
                # missing all other required fields
            })

    def test_disposition_normalisation(self):
        """Legacy disposition values are normalised."""
        obj = Phase1TurnOutput.model_validate({
            "confidence_score": 0.5,
            "escalation_required": False,
            "red_flags_triggered": [],
            "rules_triggered": [],
            "next_action": "ASK_QUESTION",
            "disposition": "URGENT_CARE",  # legacy → URGENT
        })
        assert obj.disposition == Phase1Disposition.URGENT

    def test_safe_escalation_helper(self):
        obj = safe_phase1_escalation("test-reason")
        assert obj.escalation_required is True
        assert obj.next_action == Phase1NextAction.ESCALATE_HUMAN
        assert obj.confidence_score == 0.0
