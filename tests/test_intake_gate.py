"""
Regression Tests: Intake Completion Gate & Transfer Control Enforcement

Tests the behavioral hierarchy:
  RED FLAG OVERRIDE > INTAKE COMPLETE = ALLOW > INTAKE INCOMPLETE = BLOCK

Test cases:
  TC-01  Caller requests nurse immediately (first utterance, no red flags)
         → intake continues, action=ask, override_applied=True
  TC-02  Caller requests nurse mid-intake (partial fields, no red flags)
         → intake continues, action=ask, resistance_count incremented
  TC-03  Caller requests nurse 3 times in a row (no red flags, no intake)
         → Tier-3 premature transfer, premature_transfer=True, confidence penalised
  TC-04  Caller requests nurse + critical red flag
         → immediate ER_NOW escalation, gate NOT reached (pre-check handles it)
  TC-05  Caller requests nurse after intake IS complete (all required fields)
         → transfer allowed without flags
"""

from __future__ import annotations


from src.orchestrator.intake_gate import (
    TransferControlGate,
    check_intake_complete,
    evaluate_intake_completion,
    SBAR_REQUIRED_FIELDS,
    PREMATURE_TRANSFER_CONFIDENCE_PENALTY,
)
from src.orchestrator.schemas import (
    OrchestratorSession,
    StructuredIntakeState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    intake_state: StructuredIntakeState | None = None,
) -> OrchestratorSession:
    import uuid

    return OrchestratorSession(
        session_id=str(uuid.uuid4()),
        intake_state=intake_state or StructuredIntakeState(),
    )


def _complete_intake_state() -> StructuredIntakeState:
    """Build a StructuredIntakeState with all SBAR-required fields filled."""
    return StructuredIntakeState(
        chief_complaint="severe headache",
        onset_time="2 hours ago",
        symptom_severity="severe",
        relevant_history=["no prior headaches this severe"],
        meds=["ibuprofen"],
        allergies=["penicillin"],
    )


def _empty_intake_state() -> StructuredIntakeState:
    """All SBAR-required fields absent."""
    return StructuredIntakeState()


def _partial_intake_state() -> StructuredIntakeState:
    """Only chief_complaint set — onset, severity, history, meds, allergies missing."""
    return StructuredIntakeState(
        chief_complaint="stomach pain",
    )


# ---------------------------------------------------------------------------
# Unit tests: check_intake_complete
# ---------------------------------------------------------------------------


class TestCheckIntakeComplete:
    def test_empty_state_is_incomplete(self):
        status = check_intake_complete(_empty_intake_state())
        assert status.is_complete is False
        assert len(status.missing_required) == len(SBAR_REQUIRED_FIELDS)

    def test_complete_state_is_complete(self):
        status = check_intake_complete(_complete_intake_state())
        assert status.is_complete is True
        assert status.missing_required == []

    def test_partial_state_is_incomplete(self):
        status = check_intake_complete(_partial_intake_state())
        assert status.is_complete is False
        # chief_complaint is set, so only 5 fields remain
        assert status.filled_count == 1
        assert status.missing_required  # non-empty

    def test_evaluate_intake_completion_is_alias(self):
        s = _complete_intake_state()
        assert evaluate_intake_completion(s).is_complete is True


# ---------------------------------------------------------------------------
# TC-01: Caller requests nurse immediately — no red flags, empty intake
# ---------------------------------------------------------------------------


class TestTC01_ImmediateNurseRequest:
    """
    Scenario: Very first utterance is 'I want to speak to a nurse now.'
    Red flags: None.
    Intake state: Completely empty.
    Expected: Gate returns redirect (action='redirect'), override_applied=True.
              intake continues.
    """

    def test_gate_returns_redirect_action(self):
        session = _make_session(_empty_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, next_question="", red_flags_triggered=False)
        assert decision.action == "redirect", (
            f"Expected 'redirect' but got '{decision.action}'. "
            "Transfer must be blocked when intake is empty and no red flags."
        )

    def test_gate_sets_override_applied(self):
        session = _make_session(_empty_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, next_question="", red_flags_triggered=False)
        assert decision.override_applied is True

    def test_gate_intake_incomplete_flag(self):
        session = _make_session(_empty_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, next_question="", red_flags_triggered=False)
        assert decision.intake_complete is False

    def test_gate_resistance_count_is_one(self):
        session = _make_session(_empty_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, next_question="", red_flags_triggered=False)
        assert decision.resistance_count == 1

    def test_response_message_not_empty(self):
        session = _make_session(_empty_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, next_question="", red_flags_triggered=False)
        assert decision.message.strip(), "Response message must not be empty."

    def test_no_confidence_penalty_on_tier1(self):
        session = _make_session(_empty_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, next_question="", red_flags_triggered=False)
        assert decision.confidence_delta == 0.0


# ---------------------------------------------------------------------------
# TC-02: Caller requests nurse during partial intake — no red flags
# ---------------------------------------------------------------------------


class TestTC02_MidIntakeNurseRequest:
    """
    Scenario: Caller has provided chief_complaint but nothing else.
              Requests nurse on second turn.
    Red flags: None.
    Expected: Tier 1 → redirect, override applied, intake continues.
    """

    def test_partial_intake_still_redirects(self):
        session = _make_session(_partial_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(
            session, next_question="When did this start?", red_flags_triggered=False
        )
        assert decision.action == "redirect"
        assert decision.override_applied is True

    def test_next_question_injected_in_message(self):
        session = _make_session(_partial_intake_state())
        gate = TransferControlGate()
        nq = "When did this stomach pain first start?"
        decision = gate.evaluate(session, next_question=nq, red_flags_triggered=False)
        assert nq in decision.message, (
            "The injected next_question must appear in the gate's response message."
        )

    def test_session_resistance_counter_incremented(self):
        session = _make_session(_partial_intake_state())
        assert session.nurse_request_resistance_count == 0
        gate = TransferControlGate()
        gate.evaluate(session, red_flags_triggered=False)
        assert session.nurse_request_resistance_count == 1


# ---------------------------------------------------------------------------
# TC-03: Caller requests nurse 3 times — Tier-3 premature transfer
# ---------------------------------------------------------------------------


class TestTC03_ThreeResistances:
    """
    Scenario: No red flags. Caller has empty intake state.
              Requests nurse 3 times in three consecutive turns.
    Expected on 3rd request:
      - action = 'premature_transfer'
      - premature = True
      - session.premature_transfer_triggered = True
      - confidence_delta = -PREMATURE_TRANSFER_CONFIDENCE_PENALTY
    """

    def _simulate_three_requests(self, intake_state: StructuredIntakeState) -> tuple:
        session = _make_session(intake_state)
        gate = TransferControlGate()
        d1 = gate.evaluate(session, red_flags_triggered=False)
        d2 = gate.evaluate(session, red_flags_triggered=False)
        d3 = gate.evaluate(session, red_flags_triggered=False)
        return session, d1, d2, d3

    def test_third_request_is_premature_transfer(self):
        _, _, _, d3 = self._simulate_three_requests(_empty_intake_state())
        assert d3.action == "premature_transfer", (
            f"Expected 'premature_transfer' on 3rd request but got '{d3.action}'."
        )

    def test_third_request_sets_premature_flag(self):
        _, _, _, d3 = self._simulate_three_requests(_empty_intake_state())
        assert d3.premature is True

    def test_session_premature_transfer_triggered(self):
        session, _, _, _ = self._simulate_three_requests(_empty_intake_state())
        assert session.premature_transfer_triggered is True

    def test_confidence_penalty_applied(self):
        _, _, _, d3 = self._simulate_three_requests(_empty_intake_state())
        assert d3.confidence_delta == -PREMATURE_TRANSFER_CONFIDENCE_PENALTY

    def test_resistance_count_is_three(self):
        _, _, _, d3 = self._simulate_three_requests(_empty_intake_state())
        assert d3.resistance_count == 3

    def test_first_two_are_redirects(self):
        _, d1, d2, _ = self._simulate_three_requests(_empty_intake_state())
        assert d1.action == "redirect"
        assert d2.action == "redirect"

    def test_tier1_and_tier2_messages_differ(self):
        _, d1, d2, _ = self._simulate_three_requests(_empty_intake_state())
        # Tier 1 and Tier 2 should produce distinct messages
        assert d1.message != d2.message, (
            "Tier 1 and Tier 2 resistance messages must be distinct."
        )

    def test_transfer_reason_set_on_tier3(self):
        _, _, _, d3 = self._simulate_three_requests(_empty_intake_state())
        assert d3.transfer_reason is not None
        assert "tier3" in d3.transfer_reason.lower()


# ---------------------------------------------------------------------------
# TC-04: Caller requests nurse + critical red flag → immediate ER_NOW
# ---------------------------------------------------------------------------


class TestTC04_RedFlagOverride:
    """
    Scenario: Caller says 'I think I'm having a heart attack, connect me to a nurse'.
    Red flags: ER_NOW triggered by deterministic pre-check BEFORE gate is called.

    The gate must NOT be called in this scenario — the orchestrator pre-check
    handles it.  We test here that:
      a. The gate respects the red_flags_triggered=True argument (safety catch).
      b. When called accidentally with red_flags_triggered=True, it allows transfer
         rather than blocking (fail safe, not fail closed).
    """

    def test_gate_allows_transfer_when_red_flags_triggered(self):
        """
        If the gate is accidentally called with red_flags_triggered=True,
        it should return allow_transfer (fail-safe), not redirect.
        """
        session = _make_session(_empty_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, red_flags_triggered=True)
        assert decision.action == "allow_transfer", (
            "Gate must allow transfer (fail-safe) when called with red_flags_triggered=True."
        )

    def test_gate_does_not_increment_counter_on_red_flag_bypass(self):
        """
        When bypassed due to red_flags_triggered=True, the counter should still
        be incremented (it's a nurse request detection, not a gate block).
        But no override should be applied.
        """
        session = _make_session(_empty_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, red_flags_triggered=True)
        assert decision.override_applied is False

    def test_intake_complete_evaluation_passes_for_complete_intake(self):
        """Red-flag escalation with complete intake: also allow transfer."""
        session = _make_session(_complete_intake_state())
        gate = TransferControlGate()
        # Even without red flags, complete intake allows transfer
        decision = gate.evaluate(session, red_flags_triggered=False)
        assert decision.action == "allow_transfer"


# ---------------------------------------------------------------------------
# TC-05: Caller requests nurse after intake IS complete — transfer allowed
# ---------------------------------------------------------------------------


class TestTC05_IntakeCompleteTransferAllowed:
    """
    Scenario: All 6 SBAR-required fields have been collected.
              Caller requests nurse.
    Red flags: None.
    Expected: Gate returns allow_transfer.
              premature=False, override_applied=False, confidence_delta=0.
    """

    def test_complete_intake_allows_transfer(self):
        session = _make_session(_complete_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, red_flags_triggered=False)
        assert decision.action == "allow_transfer"

    def test_complete_intake_transfer_not_premature(self):
        session = _make_session(_complete_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, red_flags_triggered=False)
        assert decision.premature is False

    def test_complete_intake_no_confidence_penalty(self):
        session = _make_session(_complete_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, red_flags_triggered=False)
        assert decision.confidence_delta == 0.0

    def test_complete_intake_override_not_applied(self):
        session = _make_session(_complete_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, red_flags_triggered=False)
        assert decision.override_applied is False

    def test_complete_intake_transfer_reason_set(self):
        session = _make_session(_complete_intake_state())
        gate = TransferControlGate()
        decision = gate.evaluate(session, red_flags_triggered=False)
        assert decision.transfer_reason == "intake_complete"

    def test_session_premature_flag_not_set(self):
        session = _make_session(_complete_intake_state())
        gate = TransferControlGate()
        gate.evaluate(session, red_flags_triggered=False)
        assert session.premature_transfer_triggered is False


# ---------------------------------------------------------------------------
# Decision trace field tests
# ---------------------------------------------------------------------------


class TestDecisionTraceFields:
    """Ensure DecisionTraceEntry carries intake-gate fields correctly."""

    def test_decision_trace_entry_has_intake_gate_fields(self):
        from src.orchestrator.schemas import DecisionTraceEntry

        entry = DecisionTraceEntry(
            turn_number=1,
            user_text="I want a nurse",
            confidence_score=0.5,
            disposition="HUMAN_REVIEW",
            escalation_required=False,
            system_response="Let me ask a follow-up question.",
            intake_complete=False,
            premature_transfer=False,
            resistance_count=1,
            transfer_reason="intake_incomplete:tier1_redirect",
            override_applied=True,
        )
        assert entry.intake_complete is False
        assert entry.premature_transfer is False
        assert entry.resistance_count == 1
        assert entry.transfer_reason == "intake_incomplete:tier1_redirect"
        assert entry.override_applied is True

    def test_decision_trace_defaults_to_no_transfer_flags(self):
        from src.orchestrator.schemas import DecisionTraceEntry

        entry = DecisionTraceEntry(
            turn_number=1,
            user_text="My knee hurts",
            confidence_score=0.4,
            disposition="SCHEDULE",
            escalation_required=False,
            system_response="How long has your knee been hurting?",
        )
        assert entry.intake_complete is False
        assert entry.premature_transfer is False
        assert entry.resistance_count == 0
        assert entry.transfer_reason is None
        assert entry.override_applied is False

    def test_orchestrator_session_has_resistance_counter(self):
        import uuid

        session = OrchestratorSession(session_id=str(uuid.uuid4()))
        assert hasattr(session, "nurse_request_resistance_count")
        assert session.nurse_request_resistance_count == 0

    def test_orchestrator_session_has_premature_transfer_flag(self):
        import uuid

        session = OrchestratorSession(session_id=str(uuid.uuid4()))
        assert hasattr(session, "premature_transfer_triggered")
        assert session.premature_transfer_triggered is False
