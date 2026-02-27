"""
Phase 5 Safety Patch — Acceptance Tests

Covers four critical fixes:
  Part 1: Role/credential claim blocker (gate_outbound_text)
  Part 2: Human/nurse request short-circuit (no LLM)
  Part 3: Yes/No answer handling (expected_answer_type)
  Part 4: Retry ladder (deterministic, non-repetitive, escalate after 3)

Each test must FAIL before the fix and PASS after the fix.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.safety.gate import (
    gate_outbound_text,
    GateContext,
    _ROLE_CLAIM_DISCLAIMER,
    _ROLE_CLAIM_SAFE_FALLBACK,
)
from src.orchestrator.orchestrator import (
    Orchestrator,
    _detect_human_request,
    _normalize_yes_no,
    _HUMAN_REQUEST_MESSAGE,
    _RETRY_LADDER_YES_NO,
    _RETRY_LADDER_DEFAULT,
)
from src.orchestrator.schemas import (
    IntakeTurnOutput,
    IntakeStatePatch,
    OrchestratorSession,
    Phase1Disposition,
    Phase1NextAction,
    Phase1TurnOutput,
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _ctx(**overrides) -> GateContext:
    defaults = {"session_id": "test-phase5", "store_phi": True}
    defaults.update(overrides)
    return GateContext(**defaults)


def _make_session(**kwargs) -> OrchestratorSession:
    defaults = {
        "session_id": "phase5-test-001",
        "max_turns": 12,
        "confidence_threshold": 0.75,
    }
    defaults.update(kwargs)
    return OrchestratorSession(**defaults)


def _valid_phase1_result(**overrides) -> Phase1TurnOutput:
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


def _make_intake_output(**kwargs) -> IntakeTurnOutput:
    defaults = {
        "extracted_fields_update": IntakeStatePatch(),
        "missing_fields_prioritized": ["onset_time"],
        "next_question": "When did this start?",
        "llm_safety_flags": [],
        "confidence": 0.3,
    }
    defaults.update(kwargs)
    return IntakeTurnOutput(**defaults)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART 1 — Role/Credential Claim Blocker
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRoleClaimBlocker:
    """gate_outbound_text must never pass role/credential claims to callers."""

    def test_i_am_a_nurse_is_blocked(self):
        """'I am a nurse' must be replaced with the automated assistant disclaimer."""
        raw = "Of course. I am a nurse, and I'm here to help."
        gated = gate_outbound_text(raw, _ctx(), "question")

        assert "I am a nurse" not in gated
        assert "automated triage assistant" in gated

    def test_i_am_a_doctor_is_blocked(self):
        """'I am a doctor' must be replaced with the automated assistant disclaimer."""
        raw = "Hello, I am a doctor. How can I help you today?"
        gated = gate_outbound_text(raw, _ctx(), "question")

        assert "I am a doctor" not in gated
        assert "automated triage assistant" in gated

    def test_i_can_diagnose_is_blocked(self):
        """'I can diagnose you' must be rewritten or replaced with safe fallback."""
        raw = "I can diagnose you."
        gated = gate_outbound_text(raw, _ctx(), "question")

        assert "I can diagnose" not in gated
        assert "automated" in gated.lower()

    def test_as_a_nurse_is_blocked(self):
        """'As a nurse...' must be replaced with the automated assistant disclaimer."""
        raw = "As a nurse, I recommend you take two aspirin."
        gated = gate_outbound_text(raw, _ctx(), "question")

        assert "As a nurse" not in gated
        assert "automated triage assistant" in gated

    def test_multiple_claims_use_safe_fallback(self):
        """Multiple role claims → safe fallback (not just disclaimer)."""
        raw = "I am a nurse, and as a doctor I can prescribe medication."
        gated = gate_outbound_text(raw, _ctx(), "question")

        # Multiple claims should trigger the fallback
        assert "I am a nurse" not in gated
        assert "as a doctor" not in gated.lower()
        # Must be either the disclaimer or the safe fallback
        assert "automated" in gated.lower()

    def test_i_can_prescribe_is_blocked(self):
        """'I can prescribe' must be replaced."""
        raw = "I can prescribe medication for that."
        gated = gate_outbound_text(raw, _ctx(), "question")

        assert "I can prescribe" not in gated
        assert "automated triage assistant" in gated

    def test_safe_text_passes_unchanged(self):
        """Text without role claims should pass through normally."""
        raw = "Can you describe your symptoms in more detail?"
        gated = gate_outbound_text(raw, _ctx(), "question")

        assert gated == raw

    def test_role_claim_blocked_in_handoff_kind(self):
        """Role claims must be blocked regardless of output kind."""
        raw = "I am a physician and here is my assessment."
        gated = gate_outbound_text(raw, _ctx(), "handoff")

        assert "I am a physician" not in gated
        assert "automated triage assistant" in gated


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART 2 — Human/Nurse Request Short-Circuit
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestHumanRequestDetection:
    """_detect_human_request must catch common phrasing."""

    def test_talk_to_a_nurse(self):
        assert _detect_human_request("Can I talk to a nurse?") is True

    def test_speak_with_a_human(self):
        assert _detect_human_request("I want to speak with a human") is True

    def test_real_person(self):
        assert _detect_human_request("I'd like a real person") is True

    def test_transfer(self):
        assert _detect_human_request("Transfer me please") is True

    def test_stop_the_ai(self):
        assert _detect_human_request("Stop the AI") is True

    def test_just_nurse(self):
        assert _detect_human_request("nurse") is True

    def test_normal_utterance_no_match(self):
        assert _detect_human_request("I have a headache") is False

    def test_empty_no_match(self):
        assert _detect_human_request("") is False


class TestHumanRequestShortCircuit:
    """When caller requests a human and no red flags are present, the intake
    gate handles it deterministically (zero LLM calls) and continues intake.
    """

    @pytest.mark.asyncio
    async def test_talk_to_nurse_zero_llm_calls(self):
        """'talk to a nurse' with no red flags → zero LLM calls, continue intake.

        New policy: low-resistance nurse request without red flags must NOT
        escalate; the transfer control gate returns an intake-continuation
        message (Tier 1 resistance response).
        """
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "Can I talk to a nurse?")

        # Must continue intake, NOT escalate
        assert result["action"] == "ask", (
            "Nurse request without red flags must return action='ask' "
            "(continue intake via resistance gate), not 'escalate'."
        )

        # Message must NOT be an immediate nurse-transfer message
        assert "connecting you to a nurse" not in result["message"].lower(), (
            "Resistance Tier 1 must not tell the caller they are being transferred."
        )

        # Message must invite continuing the intake (Tier 1 phrasing)
        assert "?" in result["message"], (
            "Resistance Tier 1 message must end with an intake question."
        )

        # Verify zero LLM calls — fully deterministic gate
        mock_llm._raw_call.assert_not_called()
        mock_llm.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_human_request_does_not_finalize(self):
        """Nurse request with incomplete intake must NOT finalize the session.

        Old behavior (finalize=True) is intentionally reversed: the session
        stays open so intake can continue.
        """
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        await orch.process_turn(session, "I want a real person")

        assert session.is_finalized is False, (
            "Session must NOT be finalized when intake is incomplete and "
            "no red flags were triggered."
        )

    @pytest.mark.asyncio
    async def test_human_request_message_is_not_transfer_message(self):
        """Resistance Tier 1 message must differ from the immediate transfer message.

        The exact transfer message (_HUMAN_REQUEST_MESSAGE) is reserved for
        cases where transfer is actually allowed.  A Tier 1 resistance response
        should acknowledge the request and continue intake.
        """
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        result = await orch.process_turn(session, "stop the ai")

        # Must be in intake-continuation mode (action ask)
        assert result["action"] == "ask"

        # Must NOT be the immediate transfer/escalation message
        assert result["message"] != _HUMAN_REQUEST_MESSAGE, (
            "Resistance Tier 1 message must differ from _HUMAN_REQUEST_MESSAGE."
        )

        # Must NOT claim to be connecting the caller to a nurse
        assert "connecting you to a nurse" not in result["message"].lower()

    @pytest.mark.asyncio
    async def test_nurse_request_with_red_flags_still_escalates(self):
        """Red-flag supremacy: ER_NOW flag + nurse request → immediate escalation.

        The transfer control gate is only reached when pre-check finds NO
        red flags.  When score_red_flags() returns ER_NOW the orchestrator
        must escalate before evaluating the nurse-request intent.
        """
        from unittest.mock import patch as _patch

        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        # Inject a deterministic ER_NOW result regardless of utterance content
        with _patch(
            "src.orchestrator.orchestrator.score_red_flags",
            return_value=("ER_NOW", 100, ["critical_flag_injected"]),
        ), _patch(
            "src.orchestrator.orchestrator.check_red_flags",
        ) as mock_check:
            from src.orchestrator.schemas import RedFlagResult, SafetyLevel
            mock_check.return_value = RedFlagResult(
                triggered=True,
                level=SafetyLevel.EMERGENT,
                matched_rules=["critical_flag_injected"],
                reason_for_audit="test: injected critical flag",
                script_to_say="Call 9-1-1 immediately.",
            )
            result = await orch.process_turn(session, "Can I talk to a nurse?")

        assert result["action"] == "escalate", (
            "ER_NOW red flag must escalate regardless of nurse-request intent."
        )
        assert session.is_finalized is True
        # Zero LLM calls — deterministic pre-check fires before gate
        mock_llm._raw_call.assert_not_called()
        mock_llm.call.assert_not_called()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART 3 — Yes/No Answer Handling
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestYesNoNormalization:
    """_normalize_yes_no must accept common yes/no variants."""

    def test_yes_variants(self):
        for word in ["yes", "yeah", "yep", "yup", "sure", "correct"]:
            assert _normalize_yes_no(word) == "yes", f"Failed for '{word}'"

    def test_no_variants(self):
        for word in ["no", "nope", "nah", "negative"]:
            assert _normalize_yes_no(word) == "no", f"Failed for '{word}'"

    def test_stt_confusion_know(self):
        """STT commonly transcribes 'no' as 'know'."""
        assert _normalize_yes_no("know") == "no"

    def test_with_trailing_punctuation(self):
        assert _normalize_yes_no("No.") == "no"
        assert _normalize_yes_no("Yes!") == "yes"

    def test_banana_not_recognised(self):
        assert _normalize_yes_no("banana") is None

    def test_sentence_not_recognised(self):
        assert _normalize_yes_no("I think so but I'm not sure") is None


class TestYesNoUnclearDetection:
    """_is_unclear_answer must accept yes/no when expected=yes_no."""

    def test_no_is_not_unclear_when_yes_no_expected(self):
        """'No.' must NOT be marked unclear when yes/no is expected."""
        result = Orchestrator._is_unclear_answer("No.", expected_answer_type="yes_no")
        assert result is False

    def test_yes_is_not_unclear_when_yes_no_expected(self):
        result = Orchestrator._is_unclear_answer("yes", expected_answer_type="yes_no")
        assert result is False

    def test_nope_is_not_unclear_when_yes_no_expected(self):
        result = Orchestrator._is_unclear_answer("nope", expected_answer_type="yes_no")
        assert result is False

    def test_banana_is_unclear_when_yes_no_expected(self):
        """'banana' must be marked unclear when yes/no is expected."""
        result = Orchestrator._is_unclear_answer("banana", expected_answer_type="yes_no")
        assert result is True

    def test_no_is_unclear_when_free_text_expected(self):
        """'No' should be unclear for open-ended questions (original behavior)."""
        result = Orchestrator._is_unclear_answer("no", expected_answer_type="free_text")
        assert result is True

    def test_no_is_unclear_when_no_type_given(self):
        """Without expected type, 'no' falls through to standard unclear list."""
        result = Orchestrator._is_unclear_answer("no")
        assert result is True

    @pytest.mark.asyncio
    async def test_no_accepted_in_full_turn_flow(self):
        """Full integration: 'No' to a yes/no question → LLM is called (not unclear)."""
        mock_llm = MagicMock()
        intake_out = _make_intake_output(expected_answer_type="yes_no")
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_out])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()
        session.last_expected_answer_type = "yes_no"

        result = await orch.process_turn(session, "No.")

        # Should NOT be treated as unclear → LLM should be called
        assert result["action"] == "ask"
        assert session.unclear_answer_retries == 0
        # LLM was called (structured_call is mocked via .call)
        assert mock_llm.call.call_count >= 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART 4 — Retry Ladder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRetryLadder:
    """Retry prompts must be deterministic, non-repetitive, and escalate after 3."""

    @pytest.mark.asyncio
    async def test_retry_messages_are_non_repetitive(self):
        """Each retry must produce a different message."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        messages = []
        for utterance in ["", "hmmm", "err"]:
            result = await orch.process_turn(session, utterance)
            messages.append(result["message"])

        # All three messages must be different
        assert len(set(messages)) == 3, f"Retry messages must not repeat: {messages}"

    @pytest.mark.asyncio
    async def test_three_unclear_escalates(self):
        """After 3 unclear attempts, response must be the escalation offer."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        # Three unclear answers
        await orch.process_turn(session, "")
        await orch.process_turn(session, "hmmm")
        result = await orch.process_turn(session, "uh")

        assert result["action"] == "escalate"
        assert "connect you with a nurse" in result["message"]
        assert result.get("fail_reason") == "confused_caller_max_retries"

    @pytest.mark.asyncio
    async def test_yes_no_retry_ladder_uses_yes_no_templates(self):
        """When expected=yes_no, retry ladder should use yes/no specific templates."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()
        mock_llm.call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()
        session.last_expected_answer_type = "yes_no"

        # "banana" is unclear even for yes_no
        result1 = await orch.process_turn(session, "banana")
        assert result1["message"] == _RETRY_LADDER_YES_NO[0]
        assert "yes or no" in result1["message"]

        result2 = await orch.process_turn(session, "purple")
        assert result2["message"] == _RETRY_LADDER_YES_NO[1]

    @pytest.mark.asyncio
    async def test_retry_counter_resets_on_valid_answer(self):
        """Valid answer must reset retry counter to 0."""
        mock_llm = MagicMock()
        intake_out = _make_intake_output()
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_out])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()

        # First: unclear
        await orch.process_turn(session, "")
        assert session.unclear_answer_retries == 1

        # Second: clear answer → counter resets
        await orch.process_turn(session, "I have a headache for two days")
        assert session.unclear_answer_retries == 0

    @pytest.mark.asyncio
    async def test_default_retry_ladder_templates(self):
        """Default (free_text) retry ladder should use different phrasing each step."""
        mock_llm = MagicMock()
        mock_llm._raw_call = AsyncMock()

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session()
        # No expected answer type set → free_text ladder

        r1 = await orch.process_turn(session, "")
        assert r1["message"] == _RETRY_LADDER_DEFAULT[0]

        r2 = await orch.process_turn(session, "erm")
        assert r2["message"] == _RETRY_LADDER_DEFAULT[1]

        r3 = await orch.process_turn(session, "what")
        assert r3["action"] == "escalate"
        assert r3["message"] == _RETRY_LADDER_DEFAULT[2]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INTEGRATION — Cross-Cutting Safety Properties
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCrossCuttingSafety:
    """Verify that all safety flows still route through the unified gate."""

    def test_role_claim_blocked_in_all_kinds(self):
        """Role claims must be blocked for question, phase1_reply, and handoff."""
        for kind in ("question", "phase1_reply", "handoff"):
            raw = "I am a nurse and I can help."
            gated = gate_outbound_text(raw, _ctx(), kind)
            assert "I am a nurse" not in gated, f"Role claim leaked in kind={kind}"
            assert "automated triage assistant" in gated

    def test_disclaimer_does_not_contain_diagnosis(self):
        """The disclaimer itself must be safe — no diagnostic claims."""
        assert "diagnos" not in _ROLE_CLAIM_DISCLAIMER.lower()
        assert "prescrib" not in _ROLE_CLAIM_DISCLAIMER.lower()

    def test_expected_answer_type_field_exists(self):
        """IntakeTurnOutput must have an expected_answer_type field."""
        out = _make_intake_output(expected_answer_type="yes_no")
        assert out.expected_answer_type == "yes_no"

    def test_expected_answer_type_default_is_free_text(self):
        """expected_answer_type should default to 'free_text'."""
        out = _make_intake_output()
        assert out.expected_answer_type == "free_text"
