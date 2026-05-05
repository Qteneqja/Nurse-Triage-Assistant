"""Healthcare dynamic intake completeness checks.

This is intentionally lightweight and deterministic. It prevents non-emergency
healthcare calls from finalizing before a clinically useful picture exists,
while leaving emergency/red-flag overrides to the orchestrator safety gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.orchestrator.schemas import OrchestratorSession, StructuredIntakeState


MIN_DYNAMIC_TURNS_BEFORE_ROUTINE_FINALIZE = 4

_GENERIC_LOCATION_REQUIRED_KEYWORDS = (
    "ache",
    "injury",
    "pain",
    "rash",
    "swelling",
)

_BODY_AREA_KEYWORDS = {
    "abdomen": "abdomen",
    "abdominal": "abdomen",
    "ankle": "ankle",
    "arm": "arm",
    "back": "back",
    "belly": "abdomen",
    "chest": "chest",
    "ear": "ear",
    "foot": "foot",
    "head": "head",
    "hip": "hip",
    "knee": "knee",
    "leg": "leg",
    "neck": "neck",
    "shoulder": "shoulder",
    "stomach": "abdomen",
    "throat": "throat",
    "wrist": "wrist",
}

_DIFFUSE_LOCATION_PHRASES = (
    "all around",
    "all over",
    "diffuse",
    "entire stomach",
    "generalized",
    "whole abdomen",
    "whole belly",
    "whole stomach",
)


@dataclass(frozen=True)
class HealthcareIntakeCompleteness:
    """Result of deterministic healthcare intake completeness evaluation."""

    is_complete: bool
    missing_items: list[str] = field(default_factory=list)
    reason: str = "incomplete"
    minimum_dynamic_turns_met: bool = False
    dynamic_turn_count: int = 0


def evaluate_healthcare_intake_completeness(
    session: OrchestratorSession,
) -> HealthcareIntakeCompleteness:
    """Evaluate whether a healthcare session has enough intake data to finalize.

    The minimum turn threshold is a hard routine-finalization gate. Emergency
    and red-flag escalation is handled before this check by the orchestrator;
    non-emergency calls must keep gathering until both the minimum turn count
    and clinical core fields are satisfied.
    """

    state = session.intake_state
    missing = _missing_clinical_items(state)
    dynamic_turn_count = session.turn_count
    minimum_met = dynamic_turn_count >= MIN_DYNAMIC_TURNS_BEFORE_ROUTINE_FINALIZE
    fields_complete = not missing
    is_complete = minimum_met and fields_complete

    if is_complete:
        reason = "complete"
    elif fields_complete and not minimum_met:
        reason = "minimum_dynamic_turns_not_met"
    else:
        reason = "missing_clinical_items"

    return HealthcareIntakeCompleteness(
        is_complete=is_complete,
        missing_items=missing,
        reason=reason,
        minimum_dynamic_turns_met=minimum_met,
        dynamic_turn_count=dynamic_turn_count,
    )


def _missing_clinical_items(state: StructuredIntakeState) -> list[str]:
    missing: list[str] = []

    if not state.chief_complaint:
        missing.append("chief_complaint")
    if not state.onset_time:
        missing.append("onset_duration")
    if not state.symptom_severity or state.symptom_severity == "unknown":
        missing.append("severity")
    if _location_relevant(state.chief_complaint) and not _location_satisfied(state):
        missing.append("location")
    if not _has_associated_symptoms_or_background(state):
        missing.append("associated_symptoms_or_relevant_history")

    return missing


def _location_relevant(chief_complaint: str | None) -> bool:
    if not chief_complaint:
        return False
    text = chief_complaint.lower()
    if infer_healthcare_location_from_text(text):
        return False
    return any(keyword in text for keyword in _GENERIC_LOCATION_REQUIRED_KEYWORDS)


def _location_satisfied(state: StructuredIntakeState) -> bool:
    return bool(state.location or infer_healthcare_location(state))


def infer_healthcare_location(state: StructuredIntakeState) -> str | None:
    """Infer a location when the caller stated it in the complaint/notes."""

    return infer_healthcare_location_from_text(
        " ".join(
            part
            for part in (
                state.location,
                state.chief_complaint,
                state.notes,
            )
            if part
        )
    )


def infer_healthcare_location_from_text(text: str | None) -> str | None:
    if not text:
        return None

    lowered = text.lower()
    if any(phrase in lowered for phrase in _DIFFUSE_LOCATION_PHRASES):
        if any(
            term in lowered for term in ("abdomen", "abdominal", "belly", "stomach")
        ):
            return "diffuse abdomen"
        return "diffuse/generalized"

    for keyword, label in _BODY_AREA_KEYWORDS.items():
        if keyword in lowered:
            return label

    return None


def _has_associated_symptoms_or_background(state: StructuredIntakeState) -> bool:
    return bool(
        state.relevant_history
        or state.red_flags_reported
        or state.meds
        or state.allergies
        or state.vitals_if_known
        or state.pregnancy_status
        or state.notes
    )
