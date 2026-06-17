"""Deterministic rules for the minimal collision-intake workflow.

Pure intake hygiene only: a completeness check (do we have what the specialist
needs?) plus parse helpers and a small estimate/advice-question detector used ONLY
to deflect to the specialist. NO triage, coverage, fault, cost, or repair decision
is ever made here. No LLM.
"""

from __future__ import annotations

import re
from typing import Any

from src.verticals.collision_intake_min.constants import (
    COLLISION_MIN_DISCLAIMER,
    COLLISION_MIN_REQUIRED_FIELDS,
)
from src.verticals.collision_intake_min.schemas import (
    CollisionMinAssessment,
    CollisionMinIntake,
)


def classify_collision_min_intake(
    intake: CollisionMinIntake,
    dynamic_text: str = "",
) -> CollisionMinAssessment:
    """Return an intake-completeness assessment (capture -> handoff).

    READY_FOR_SPECIALIST when every required field (plus vehicle_location when the
    vehicle is not drivable) is present; otherwise CALLBACK_NEEDED with the missing
    list. Flags are descriptive data for the specialist, never decisions.
    """
    missing = _missing_required(intake)
    flags: list[str] = []
    rules: list[str] = ["collision_min:intake_completeness"]

    if intake.drivable_status == "not_drivable":
        flags.append("needs_tow")
        if not _present(intake.vehicle_location):
            missing.append("vehicle_location")
    elif intake.drivable_status == "drivable":
        flags.append("drivable")

    if intake.mpi_claim_opened is True:
        flags.append(
            "mpi_claim_in_hand"
            if intake.mpi_claim_number
            else "mpi_claim_open_no_number"
        )
    elif intake.mpi_claim_opened is False:
        flags.append("no_mpi_claim")

    # Estimate/coverage/cost/fault/repair-time question -> deflect (spoken),
    # never answered. This is plain hallucination-prevention, not a decision.
    if detect_estimate_request(dynamic_text) or detect_estimate_request(
        intake.damage_description or ""
    ):
        flags.append("estimate_request_deflected")
        rules.append("collision_min:estimate_question_deflected")

    missing = _dedupe(missing)
    flags = _dedupe(flags)

    if missing:
        return CollisionMinAssessment(
            disposition="CALLBACK_NEEDED",
            handoff_mode="callback",
            missing_information=missing,
            flags=flags,
            rules_triggered=_dedupe([*rules, "collision_min:callback_needed"]),
            human_review_required=False,
            confidence=0.6,
            disclaimers_given=[COLLISION_MIN_DISCLAIMER],
        )

    return CollisionMinAssessment(
        disposition="READY_FOR_SPECIALIST",
        handoff_mode="warm_transfer",
        missing_information=[],
        flags=flags,
        rules_triggered=_dedupe([*rules, "collision_min:ready_for_specialist"]),
        human_review_required=False,
        confidence=0.9,
        disclaimers_given=[COLLISION_MIN_DISCLAIMER],
    )


# ---------------------------------------------------------------------------
# Estimate / coverage / cost / fault / repair-time question detector
# ---------------------------------------------------------------------------

_ESTIMATE_REQUEST = re.compile(
    r"\bhow much\b|\bwhat(?:'?s| is| will)\b[^?]*\b(?:cost|estimate|price|charge)\b"
    r"|\bcover(?:ed|s|age)?\b|\bwill (?:mpi|insurance|my insurance)\b"
    r"|\bwhose fault\b|\bat fault\b|\bwho'?s to blame\b"
    r"|\bhow long\b[^?]*\b(?:take|repair|fix)\b|\bwhen will it be (?:done|ready|fixed)\b",
    re.IGNORECASE,
)


def detect_estimate_request(text: str | None) -> bool:
    """True if the caller is asking for an estimate/coverage/fault/timing answer."""
    if not text or not text.strip():
        return False
    return bool(_ESTIMATE_REQUEST.search(text))


# ---------------------------------------------------------------------------
# Parse helpers (deterministic; no LLM)
# ---------------------------------------------------------------------------


def parse_drivable_status(value: Any) -> str | None:
    """Map free speech to drivable | not_drivable | unknown."""
    text = _normalize(str(value or ""))
    if not text:
        return None
    if _contains_any(
        text,
        [
            "not drivable",
            "not driveable",
            "can't drive",
            "cant drive",
            "cannot drive",
            "needs a tow",
            "needs tow",
            "needs towing",
            "tow truck",
            "towed",
            "not safe to drive",
            "undrivable",
        ],
    ):
        return "not_drivable"
    if _contains_any(
        text,
        [
            "drivable",
            "driveable",
            "safe to drive",
            "can drive",
            "still drives",
            "drives fine",
        ],
    ):
        return "drivable"
    if re.search(r"\b(no|nope)\b", text):
        return "not_drivable"
    if re.search(r"\b(yes|yeah|yep)\b", text):
        return "drivable"
    if _contains_any(
        text, ["not sure", "unsure", "don't know", "dont know", "unknown"]
    ):
        return "unknown"
    return None


def parse_claim_opened(value: Any) -> bool | None:
    """Map free speech to whether an MPI claim is already opened."""
    text = _normalize(str(value or ""))
    if not text:
        return None
    if _contains_any(
        text,
        [
            "no claim",
            "not yet",
            "haven't opened",
            "havent opened",
            "no, not yet",
            "haven't filed",
            "havent filed",
            "no i haven't",
        ],
    ):
        return False
    if _contains_any(
        text,
        [
            "claim is open",
            "already opened",
            "opened a claim",
            "filed a claim",
            "have a claim",
            "claim number",
            "yes",
        ],
    ):
        return True
    if re.search(r"\b(no|nope|nah)\b", text):
        return False
    return None


def parse_vehicle_year(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    match = re.search(r"\b(19[0-9]{2}|20[0-9]{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def clean_claim_number(value: Any) -> str | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    if re.search(
        r"don'?t have|do not have|not yet|haven'?t|no number|nothing yet|no claim",
        cleaned.lower(),
    ):
        return None
    return cleaned


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _missing_required(intake: CollisionMinIntake) -> list[str]:
    data = intake.model_dump()
    return [f for f in COLLISION_MIN_REQUIRED_FIELDS if not _present(data.get(f))]


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _contains_any(text: str, phrases: list[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out
