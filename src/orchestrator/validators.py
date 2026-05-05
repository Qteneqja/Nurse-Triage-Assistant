"""
JSON Validators

Utilities for validating LLM outputs against orchestrator schemas,
and for building validated objects with safe defaults when needed.

Phase 1 additions:
- validate_phase1_output(): 2-attempt repair loop with fail-closed behaviour
- post_check_safety_gate(): THIN WRAPPER → delegates to src.safety.gate
- UNSAFE_INSTRUCTION_PATTERNS: curated unsafe-instruction detector
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

from src.orchestrator.schemas import (
    IntakeTurnOutput,
    IntakeStatePatch,
    FinalizeOutput,
    Phase1TurnOutput,
    Phase1Disposition,
    Phase1NextAction,
    SafetyFlag,
    SafetyLevel,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def extract_json(raw: str) -> Optional[dict]:
    """Extract a JSON object from raw LLM text output.

    Handles:
    - Clean JSON
    - Markdown fenced JSON (```json ... ```)
    - JSON embedded in surrounding text

    Args:
        raw: Raw string output from the LLM.

    Returns:
        Parsed dict, or None if no valid JSON found.
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip()

    # Strip markdown fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find largest JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def validate_against_schema(
    data: dict,
    schema: Type[T],
) -> tuple[Optional[T], Optional[str]]:
    """Validate a dict against a Pydantic schema.

    Args:
        data: Parsed JSON dict.
        schema: Pydantic model class.

    Returns:
        Tuple of (validated_object, error_message).
        If valid, error_message is None.
    """
    try:
        obj = schema.model_validate(data)
        return obj, None
    except ValidationError as e:
        return None, str(e)


def parse_and_validate(
    raw: str,
    schema: Type[T],
) -> tuple[Optional[T], Optional[str]]:
    """Extract JSON from raw text and validate against schema.

    Args:
        raw: Raw LLM output.
        schema: Pydantic model class.

    Returns:
        Tuple of (validated_object, error_string).
    """
    data = extract_json(raw)
    if data is None:
        return None, "No valid JSON found in LLM output"
    return validate_against_schema(data, schema)


def safe_intake_turn_default(
    fallback_question: str = "Can you tell me more about what you're experiencing?",
) -> IntakeTurnOutput:
    """Return a safe default IntakeTurnOutput for when LLM fails.

    Args:
        fallback_question: Question to ask the caller.

    Returns:
        IntakeTurnOutput with conservative defaults.
    """
    return IntakeTurnOutput(
        extracted_fields_update=IntakeStatePatch(),
        missing_fields_prioritized=[
            "chief_complaint",
            "symptom_severity",
            "onset_time",
        ],
        next_question=fallback_question,
        llm_safety_flags=[],
        confidence=0.0,
    )


def safe_finalize_default() -> FinalizeOutput:
    """Return a safe default FinalizeOutput for when LLM fails.

    Returns:
        FinalizeOutput with HUMAN_REVIEW disposition.
    """
    from src.orchestrator.schemas import DispositionCategory

    return FinalizeOutput(
        disposition=DispositionCategory.HUMAN_REVIEW,
        disposition_reasoning="Unable to complete automated clinical reasoning. Escalating for human review.",
        safety_net_instructions=[
            "If your symptoms worsen or you feel unsafe at any time, "
            "please call 9 1 1 or go to the nearest emergency room.",
        ],
        sbar_report=(
            "S: Patient completed phone triage intake. Automated reasoning unavailable.\n"
            "B: See conversation transcript for details.\n"
            "A: Unable to complete automated assessment. Manual review required.\n"
            "R: Human clinician review recommended."
        ),
        patient_summary=(
            "Thank you for providing that information. "
            "A nurse will review your case and contact you soon. "
            "If your symptoms worsen, please go to the emergency room or call 9 1 1."
        ),
        llm_safety_flags=[
            SafetyFlag(
                source="llm",
                level=SafetyLevel.ADVISORY,
                flag="Automated reasoning failed — manual review required",
                reason_for_audit="LLM-detected: Automated reasoning failed",
            )
        ],
    )


def safe_finalize_from_session(session) -> FinalizeOutput:
    """Return a conservative final output populated from collected intake data."""

    from src.orchestrator.schemas import DispositionCategory
    from src.verticals.healthcare.completeness import infer_healthcare_location

    state = session.intake_state
    patient_bits = []
    if state.caller_age is not None:
        patient_bits.append(f"{state.caller_age}-year-old")
    if state.caller_sex:
        patient_bits.append(state.caller_sex)
    patient_desc = " ".join(patient_bits) or "Patient"
    complaint = state.chief_complaint or "reported symptoms"
    onset = state.onset_time or "onset not documented"
    severity = state.symptom_severity or "severity not documented"
    location = (
        state.location or infer_healthcare_location(state) or "location not documented"
    )

    background_items: list[str] = []
    if state.relevant_history:
        background_items.append(
            "associated/history: " + ", ".join(state.relevant_history)
        )
    if state.meds:
        background_items.append("medications/supplements: " + ", ".join(state.meds))
    if state.allergies:
        background_items.append("allergies: " + ", ".join(state.allergies))
    if state.pregnancy_status:
        background_items.append(f"pregnancy status: {state.pregnancy_status}")
    if state.red_flags_reported:
        background_items.append(
            "reported red flags: " + ", ".join(state.red_flags_reported)
        )
    background = "; ".join(background_items) or "No additional background documented."

    sbar_report = (
        f"S: {patient_desc} calling about {complaint}. "
        f"Location: {location}. Onset: {onset}. Severity: {severity}.\n"
        f"B: {background}\n"
        "A: Automated final clinical reasoning was unavailable, so this case "
        "requires human clinical review using the collected intake information.\n"
        "R: Nurse/clinician review recommended. If symptoms worsen, new red flags "
        "develop, or the caller feels unsafe, they should seek emergency care."
    )

    return FinalizeOutput(
        disposition=DispositionCategory.HUMAN_REVIEW,
        disposition_reasoning=(
            "Automated clinical reasoning was unavailable. Human review is "
            "recommended based on the collected intake information."
        ),
        safety_net_instructions=[
            "If your symptoms worsen or you feel unsafe at any time, "
            "please call 9 1 1 or go to the nearest emergency room.",
        ],
        sbar_report=sbar_report,
        patient_summary=(
            "Thank you for providing that information. A nurse will review your "
            "case and contact you soon. If your symptoms worsen, please go to "
            "the emergency room or call 9 1 1."
        ),
        llm_safety_flags=[
            SafetyFlag(
                source="llm",
                level=SafetyLevel.ADVISORY,
                flag="Automated reasoning failed - manual review required",
                reason_for_audit="LLM-detected: Automated reasoning failed",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 1 — Two-attempt JSON validation with repair
# ---------------------------------------------------------------------------


async def validate_phase1_output(
    raw: str,
    repair_fn,  # async callable(bad_raw: str) -> str
    correlation_id: str = "no-cid",
) -> Tuple[Optional[Phase1TurnOutput], bool]:
    """Validate LLM raw output against Phase1TurnOutput schema.

    Attempt 1: direct parse + validate.
    Attempt 2: call repair_fn with exact validation errors, re-parse.
    If both fail → return (None, True) — caller must fail-closed.

    Args:
        raw: Raw string from LLM.
        repair_fn: Async function that accepts the bad raw string and
                   a list of error strings, and returns repaired raw string.
        correlation_id: For log correlation.

    Returns:
        Tuple (Phase1TurnOutput or None, repaired_flag).
        repaired_flag=True if repair was necessary and succeeded.
    """
    # Attempt 1
    obj, err = parse_and_validate(raw, Phase1TurnOutput)
    if obj is not None:
        return obj, False

    logger.warning(f"[VALIDATE:{correlation_id}] Phase1 attempt-1 failed: {err}")

    # Attempt 2: repair
    try:
        repaired_raw = await repair_fn(raw, err or "schema validation failure")
    except Exception as repair_exc:
        logger.error(f"[VALIDATE:{correlation_id}] Repair call raised: {repair_exc}")
        return None, False

    obj2, err2 = parse_and_validate(repaired_raw, Phase1TurnOutput)
    if obj2 is not None:
        logger.warning(f"[VALIDATE:{correlation_id}] Phase1 repaired successfully")
        return obj2, True

    logger.error(
        f"[VALIDATE:{correlation_id}] Phase1 repair also failed: {err2}. "
        "Failing closed."
    )
    return None, False


# ---------------------------------------------------------------------------
# Phase 1 — Post-Check Safety Gate
# ---------------------------------------------------------------------------

# Patterns that indicate the LLM made a diagnosis
_DIAGNOSIS_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\byou\s+(have|are\s+suffering\s+from|are\s+experiencing)\b.{0,40}\b(disease|condition|syndrome|disorder|illness|infection|cancer|tumor)\b",
        r"\bthis\s+is\s+(likely\s+)?(a\s+)?(diagnosis|case\s+of|sign\s+of)\b",
        r"\bdiagnos(is|ed|ing)\b",
        r"\bthe\s+cause\s+(is|appears\s+to\s+be)\b",
    ]
]

# Patterns that indicate unsafe clinical instructions
_UNSAFE_INSTRUCTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(take|use|apply|give)\s+(yourself\s+)?(\d+\s+)?(mg|milligram|tablet|dose|pill)\b",
        r"\byou\s+(don\s*'?\s*t|do\s+not)\s+need\s+(to\s+)?(see|call|go\s+to|visit|contact)\b",
        r"\bno\s+need\s+(for\s+)?(emergency|er|hospital|911|9\s*1\s*1|doctor)\b",
        r"\byou\s+'?re\s+fine\b",
        r"\bit\s+is\s+not\s+(serious|dangerous|urgent|an\s+emergency)\b",
        r"\bstay\s+(home|at\s+home)\s+and\s+(rest|wait)\b",
        r"\bdon\s*'?\s*t\s+(go\s+to|call|visit)\s+(the\s+)?(er|emergency|hospital|911|9\s*1\s*1)\b",
    ]
]

# Urgency level ordering — LLM may not downgrade
_URGENCY_ORDER = {
    "ER_NOW": 4,
    "URGENT": 3,
    "SCHEDULE": 2,
    "SELF_CARE": 1,
    "HUMAN_REVIEW": 0,
}


class PostCheckViolation(Exception):
    """Raised when the post-check safety gate detects a violation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def post_check_safety_gate(
    llm_response_text: str,
    previous_disposition: Optional[str],
    phase1_output: Optional[Phase1TurnOutput],
    correlation_id: str = "no-cid",
) -> None:
    """Post-LLM safety gate — THIN WRAPPER around src.safety.gate.

    Delegates text gating to the unified gate module so there is
    only ONE set of safety checks.  Retains the exception-based API
    so callers don't need to change.

    Raises:
        PostCheckViolation: If any safety rule is violated.
    """
    from src.safety.gate import gate_outbound_text, GateContext

    ctx = GateContext()
    gated = gate_outbound_text(llm_response_text, ctx, kind="phase1_reply")

    # If the gate rewrote content, a violation occurred
    if gated != llm_response_text:
        raise PostCheckViolation(
            "Gate rewrote LLM output (diagnosis/unsafe content detected)"
        )

    # Check 3: Urgency downgrade (kept here because it requires cross-turn state)
    if phase1_output is not None and previous_disposition is not None:
        prev_level = _URGENCY_ORDER.get(previous_disposition.upper(), 0)
        curr_level = _URGENCY_ORDER.get(phase1_output.disposition.value, 0)
        if curr_level < prev_level:
            raise PostCheckViolation(
                f"LLM attempted urgency downgrade: "
                f"{previous_disposition} → {phase1_output.disposition.value}"
            )

    logger.debug(f"[POST-CHECK:{correlation_id}] Passed")


def safe_phase1_escalation(reason: str = "Fail-closed escalation") -> Phase1TurnOutput:
    """Return a safe Phase1TurnOutput that forces immediate human escalation."""
    return Phase1TurnOutput(
        confidence_score=0.0,
        escalation_required=True,
        red_flags_triggered=[],
        rules_triggered=[f"fail_closed:{reason}"],
        next_action=Phase1NextAction.ESCALATE_HUMAN,
        disposition=Phase1Disposition.HUMAN_REVIEW,
    )
