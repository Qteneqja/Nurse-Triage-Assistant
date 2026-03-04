"""
Intake Completion Gate — Transfer Control Enforcement
=====================================================

This module implements backend-enforced intake completeness checking and
transfer control logic.  It is the authoritative gate that sits between
caller "transfer me" utterances and the actual escalation path.

Behavioral hierarchy (non-negotiable):
  1. Deterministic red flags → immediate escalation (NOT handled here; already
     done in the orchestrator pre-check before this gate is reached).
  2. Red-flag escalation path bypasses this gate entirely.
  3. If red_flags_triggered == False:
       a. intake_complete == True  → transfer allowed
       b. intake_complete == False → transfer BLOCKED; resistance protocol runs

Architecture:
  ┌────────────────────────────────────────────────────────────────┐
  │              CALLER UTTERS "I want a nurse"                    │
  └────────────────────────────────────────────────────────────────┘
                              │
                              ▼
               ┌──────────────────────────┐
               │  Red flags triggered?     │
               └──────────────────────────┘
                    YES │           NO │
                        ▼             ▼
               [IMMEDIATE       Check intake_complete
                ESCALATION]           │
                             ┌────────┴────────┐
                             │                 │
                        COMPLETE        NOT COMPLETE
                             │                 │
                             ▼                 ▼
                      [Allow transfer]   ResistanceHandler
                                               │
                        ┌──────────┬──────────┴──────────┐
                   count=1    count=2              count≥3
                        │          │                     │
                     Tier 1:    Tier 2:            Tier 3:
                   Acknowledge  Reaffirm        Allow prematurely
                + continue q  + continue q      (with flags set)
                        │          │                     │
                   [ask,       [ask,             [escalate,
                   override]   override]          premature=True,
                                                  confidence↓]

Usage:
    from src.orchestrator.intake_gate import TransferControlGate, evaluate_intake_completion

    gate = TransferControlGate()
    result = gate.evaluate(session, red_flags_triggered=False)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from src.orchestrator.schemas import OrchestratorSession, StructuredIntakeState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Part 1 — SBAR-Compliant Required Intake Fields
# ---------------------------------------------------------------------------

# Each entry: (field_name_on_StructuredIntakeState, human_label, is_list)
# These are the minimum fields that MUST be collected before a non-emergency
# transfer may be allowed.  Mirrors the SBAR S/B headings.
SBAR_REQUIRED_FIELDS: list[tuple[str, str, bool]] = [
    # Situation
    ("chief_complaint", "Chief complaint", False),
    ("onset_time", "Onset / duration", False),
    ("symptom_severity", "Severity", False),
    # Background
    ("relevant_history", "Associated symptoms / hx", True),
    ("meds", "Medications", True),
    ("allergies", "Allergies", True),
]

# Optional but strongly preferred — missing these does NOT block transfer
SBAR_PREFERRED_FIELDS: list[tuple[str, str, bool]] = [
    ("caller_age", "Caller age", False),
    ("caller_sex", "Caller sex", False),
]

# Minimum number of *required* fields that must be filled to consider
# intake structurally complete.  Set to len(SBAR_REQUIRED_FIELDS) for strict
# enforcement; can be lowered for progressive deployment.
INTAKE_REQUIRED_FIELD_THRESHOLD: int = len(SBAR_REQUIRED_FIELDS)  # all required


@dataclass
class IntakeCompletionStatus:
    """Snapshot of whether SBAR-required intake fields have been collected.

    Attributes:
        is_complete:        True when all required fields have values.
        missing_required:   Names of required fields still absent.
        missing_preferred:  Names of preferred (non-blocking) fields still absent.
        filled_count:       How many required fields are filled.
        required_total:     Total number of required fields.
    """

    is_complete: bool
    missing_required: List[str] = field(default_factory=list)
    missing_preferred: List[str] = field(default_factory=list)
    filled_count: int = 0
    required_total: int = len(SBAR_REQUIRED_FIELDS)


def check_intake_complete(
    intake_state: "StructuredIntakeState",
) -> IntakeCompletionStatus:
    """Evaluate whether the SBAR-required intake fields have been collected.

    Args:
        intake_state:  Current StructuredIntakeState from the session.

    Returns:
        IntakeCompletionStatus — never raises.
    """
    missing_required: list[str] = []
    missing_preferred: list[str] = []
    filled = 0

    for fname, label, is_list in SBAR_REQUIRED_FIELDS:
        val = getattr(intake_state, fname, None)
        if is_list:
            # List fields: considered filled if at least one entry
            if val:
                filled += 1
            else:
                missing_required.append(label)
        else:
            if val is not None:
                filled += 1
            else:
                missing_required.append(label)

    for fname, label, is_list in SBAR_PREFERRED_FIELDS:
        val = getattr(intake_state, fname, None)
        if is_list:
            if not val:
                missing_preferred.append(label)
        else:
            if val is None:
                missing_preferred.append(label)

    is_complete = filled >= INTAKE_REQUIRED_FIELD_THRESHOLD

    return IntakeCompletionStatus(
        is_complete=is_complete,
        missing_required=missing_required,
        missing_preferred=missing_preferred,
        filled_count=filled,
        required_total=len(SBAR_REQUIRED_FIELDS),
    )


# ---------------------------------------------------------------------------
# Part 3 — Resistance Handling Micro-Protocol
# ---------------------------------------------------------------------------

# Tier 1: Acknowledge preference + explain + continue intake
_RESISTANCE_TIER1 = (
    "I hear you — and I want to get you to our nursing staff as quickly as possible. "
    "To make sure they have everything they need to help you right away, "
    "I just need a few more pieces of information. "
    "{next_question}"
)

# Tier 2: Reaffirm safety importance + emphasise nurse needs info
_RESISTANCE_TIER2 = (
    "I completely understand your preference, and I respect that. "
    "Our nurses are ready to help, but they need this information to provide you with safe and accurate care. "
    "If we skip these steps, they may not be able to help you as effectively. "
    "This will only take a moment longer. "
    "{next_question}"
)

# Tier 3: Premature transfer allowed — logged and flagged
_RESISTANCE_TIER3_PREFIX = (
    "Understood. I'm connecting you to a nurse now. "
    "Please let them know about your {chief_complaint_or_concern}. "
    "If your symptoms worsen before they answer, please call 9-1-1 immediately."
)

# Confidence penalty applied on premature transfer
PREMATURE_TRANSFER_CONFIDENCE_PENALTY: float = 0.30


@dataclass
class TransferGateDecision:
    """Output of the TransferControlGate.

    Attributes:
        action:            "redirect" | "allow_transfer" | "premature_transfer"
        message:           Text to say to the caller.
        intake_complete:   Whether intake was complete at decision time.
        premature:         True if transfer was allowed before intake was complete.
        resistance_count:  Session-level nurse-request count at time of decision.
        transfer_reason:   Reason code for audit trail.
        override_applied:  True when the gate blocked an LLM-suggested transfer.
        confidence_delta:  Adjustment to apply to the confidence score (≤ 0).
    """

    action: str  # "redirect" | "allow_transfer" | "premature_transfer"
    message: str
    intake_complete: bool
    premature: bool = False
    resistance_count: int = 0
    transfer_reason: Optional[str] = None
    override_applied: bool = False
    confidence_delta: float = 0.0


class TransferControlGate:
    """Backend gate that enforces intake completion before nurse transfer.

    This is a pure-Python, stateless evaluator.  All state lives in the
    OrchestratorSession that is passed in.  The gate never mutates session
    directly — it returns a TransferGateDecision and the caller applies it.

    Behavioral contract:
      - red_flags_triggered == True  → caller must NOT reach this gate
        (handled upstream in orchestrator pre-check).
      - intake_complete == True      → allow transfer immediately.
      - intake_complete == False:
          resistance_count == 1  → Tier 1 response, continue intake
          resistance_count == 2  → Tier 2 response, continue intake
          resistance_count >= 3  → Tier 3, allow with flags
    """

    def evaluate(
        self,
        session: "OrchestratorSession",
        next_question: str = "",
        red_flags_triggered: bool = False,
    ) -> TransferGateDecision:
        """Evaluate whether a nurse-transfer request should be allowed.

        Callers MUST already have confirmed red_flags_triggered == False before
        invoking this method.  If red flags ARE present, skip this gate and
        escalate immediately.

        Args:
            session:              Current OrchestratorSession.
            next_question:        The next intake question to inject if blocking.
            red_flags_triggered:  Sanity-check flag; must be False.

        Returns:
            TransferGateDecision describing what the system should do.
        """
        if red_flags_triggered:
            # This should never be called with red flags — log and return a safe
            # allow so the caller can escalate through the normal red-flag path.
            logger.error(
                "[INTAKE_GATE] evaluate() called with red_flags_triggered=True. "
                "This is a programming error — skip this gate when red flags are set."
            )
            return TransferGateDecision(
                action="allow_transfer",
                message="",
                intake_complete=False,
                transfer_reason="red_flag_gate_bypass_error",
                override_applied=False,
            )

        # Increment resistance counter
        session.nurse_request_resistance_count += 1
        resistance = session.nurse_request_resistance_count

        # Check intake completeness
        status = check_intake_complete(session.intake_state)

        logger.info(
            f"[INTAKE_GATE] resistance={resistance}, "
            f"intake_complete={status.is_complete}, "
            f"missing={status.missing_required}"
        )

        if status.is_complete:
            return TransferGateDecision(
                action="allow_transfer",
                message="",
                intake_complete=True,
                premature=False,
                resistance_count=resistance,
                transfer_reason="intake_complete",
                override_applied=False,
                confidence_delta=0.0,
            )

        # Intake is NOT complete — apply resistance protocol
        if resistance == 1:
            return self._tier1(status, next_question, resistance)
        elif resistance == 2:
            return self._tier2(status, next_question, resistance)
        else:
            # resistance >= 3 → premature transfer with flagging
            return self._tier3(session, status, resistance)

    # ------------------------------------------------------------------
    # Tier builders
    # ------------------------------------------------------------------

    @staticmethod
    def _tier1(
        status: IntakeCompletionStatus,
        next_question: str,
        resistance: int,
    ) -> TransferGateDecision:
        nq = next_question or _missing_fields_question(status.missing_required)
        message = _RESISTANCE_TIER1.format(next_question=nq)
        return TransferGateDecision(
            action="redirect",
            message=message,
            intake_complete=False,
            premature=False,
            resistance_count=resistance,
            transfer_reason="intake_incomplete:tier1_redirect",
            override_applied=True,
            confidence_delta=0.0,
        )

    @staticmethod
    def _tier2(
        status: IntakeCompletionStatus,
        next_question: str,
        resistance: int,
    ) -> TransferGateDecision:
        nq = next_question or _missing_fields_question(status.missing_required)
        message = _RESISTANCE_TIER2.format(next_question=nq)
        return TransferGateDecision(
            action="redirect",
            message=message,
            intake_complete=False,
            premature=False,
            resistance_count=resistance,
            transfer_reason="intake_incomplete:tier2_redirect",
            override_applied=True,
            confidence_delta=0.0,
        )

    @staticmethod
    def _tier3(
        session: "OrchestratorSession",
        status: IntakeCompletionStatus,
        resistance: int,
    ) -> TransferGateDecision:
        chief = session.intake_state.chief_complaint or "your concern"
        message = _RESISTANCE_TIER3_PREFIX.format(chief_complaint_or_concern=chief)
        # Mark session level flag
        session.premature_transfer_triggered = True
        return TransferGateDecision(
            action="premature_transfer",
            message=message,
            intake_complete=False,
            premature=True,
            resistance_count=resistance,
            transfer_reason="caller_resistance:tier3_premature_transfer",
            override_applied=False,
            confidence_delta=-PREMATURE_TRANSFER_CONFIDENCE_PENALTY,
        )


# ---------------------------------------------------------------------------
# Helper: generate a question from the first missing field
# ---------------------------------------------------------------------------

_FIELD_QUESTION_MAP: dict[str, str] = {
    "Chief complaint": "Can you tell me what's bothering you most right now?",
    "Onset / duration": "When did this first start?",
    "Severity": "On a scale of mild, moderate, or severe, how would you describe it?",
    "Associated symptoms / hx": "Are you experiencing any other symptoms along with this?",
    "Medications": "Are you currently taking any medications?",
    "Allergies": "Do you have any known drug allergies?",
}


def _missing_fields_question(missing: list[str]) -> str:
    """Return a plain-language question for the first missing required field."""
    for label in missing:
        q = _FIELD_QUESTION_MAP.get(label)
        if q:
            return q
    if missing:
        return f"Could you tell me about your {missing[0].lower()}?"
    return "Could you share a little more information so I can help you safely?"


# ---------------------------------------------------------------------------
# Public convenience alias
# ---------------------------------------------------------------------------


def evaluate_intake_completion(
    intake_state: "StructuredIntakeState",
) -> IntakeCompletionStatus:
    """Public alias for check_intake_complete — for use in tests and API layers."""
    return check_intake_complete(intake_state)
