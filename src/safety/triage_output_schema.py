"""
Strict Triage Output Schema — Phase 1 Hardening

Defines THE canonical JSON schema for triage output.
Includes Pydantic validation, retry logic (max 2), and safe fallback.

All fields are MANDATORY. If the LLM cannot produce valid output
after 2 retries, the safe fallback is used — no exceptions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strict triage output schema
# ---------------------------------------------------------------------------

class TriageOutput(BaseModel):
    """Strict JSON schema for triage output. All fields mandatory.

    This is the single schema that ALL triage decisions must conform to.
    """
    disposition: str = Field(
        description="Triage disposition: ER_NOW | URGENT | ROUTINE | SELF_CARE | HUMAN_REVIEW"
    )
    urgency_level: str = Field(
        description="CRITICAL | HIGH | MEDIUM | LOW"
    )
    confidence_score: float = Field(
        ge=0.0, le=1.0,
        description="Confidence score 0.0–1.0"
    )
    rules_triggered: List[str] = Field(
        default_factory=list,
        description="List of rule IDs triggered"
    )
    red_flags_triggered: List[str] = Field(
        default_factory=list,
        description="List of red flag descriptions triggered"
    )
    escalation_required: bool = Field(
        description="Whether immediate escalation is required"
    )
    protocol_references: List[str] = Field(
        default_factory=list,
        description="Protocol IDs used in reasoning"
    )
    model_version: str = Field(
        default="unknown",
        description="LLM model version"
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 timestamp"
    )
    message_to_caller: Optional[str] = Field(
        default=None,
        description="Voice-friendly message to speak to caller"
    )

    @field_validator("disposition")
    @classmethod
    def _validate_disposition(cls, v: str) -> str:
        from src.shared.canonical import CANONICAL_DISPOSITION_VALUES
        upper = v.strip().upper()
        # Map legacy values before checking
        _legacy_map = {
            "SAFE": "SELF_CARE", "PCP": "SCHEDULE", "EMERGENCY": "ER_NOW",
            "URGENT_CARE": "URGENT", "SAME_DAY": "SCHEDULE", "ROUTINE": "SCHEDULE",
        }
        mapped = _legacy_map.get(upper, upper)
        if mapped not in CANONICAL_DISPOSITION_VALUES:
            raise ValueError(f"disposition must be one of {CANONICAL_DISPOSITION_VALUES}, got '{v}'")
        return mapped

    @field_validator("urgency_level")
    @classmethod
    def _validate_urgency(cls, v: str) -> str:
        allowed = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        upper = v.strip().upper()
        if upper not in allowed:
            raise ValueError(f"urgency_level must be one of {allowed}, got '{v}'")
        return upper


# ---------------------------------------------------------------------------
# Safe fallback output
# ---------------------------------------------------------------------------

SAFE_FALLBACK_OUTPUT = TriageOutput(
    disposition="HUMAN_REVIEW",
    urgency_level="HIGH",
    confidence_score=0.0,
    rules_triggered=["safe_fallback"],
    red_flags_triggered=[],
    escalation_required=True,
    protocol_references=[],
    model_version="fallback",
    timestamp=datetime.now(timezone.utc).isoformat(),
    message_to_caller=(
        "I cannot safely assess this situation. "
        "Please seek immediate medical attention or speak with a nurse."
    ),
)

SAFE_FALLBACK_MESSAGE = SAFE_FALLBACK_OUTPUT.message_to_caller


# ---------------------------------------------------------------------------
# Validation with retry (max 2 attempts)
# ---------------------------------------------------------------------------

MAX_VALIDATION_RETRIES = 2


def validate_triage_output(
    data: dict,
    attempt: int = 0,
) -> Optional[TriageOutput]:
    """Validate a dict against the TriageOutput schema.

    Retries up to MAX_VALIDATION_RETRIES times with coercion attempts.
    On final failure → returns None (caller must use SAFE_FALLBACK_OUTPUT).

    Args:
        data: Parsed JSON dict from LLM.
        attempt: Current attempt number (internal).

    Returns:
        Validated TriageOutput, or None on failure.
    """
    try:
        return TriageOutput.model_validate(data)
    except Exception as exc:
        logger.warning(
            f"[TRIAGE_SCHEMA] Validation attempt {attempt + 1} failed: {exc}"
        )

        if attempt + 1 >= MAX_VALIDATION_RETRIES:
            logger.error(
                f"[TRIAGE_SCHEMA] All {MAX_VALIDATION_RETRIES} validation attempts failed. "
                "Returning None — caller must use safe fallback."
            )
            return None

        # Attempt coercion fixes
        coerced = _attempt_coercion(data)
        return validate_triage_output(coerced, attempt + 1)


def _attempt_coercion(data: dict) -> dict:
    """Attempt to fix common LLM output issues."""
    fixed = dict(data)

    # Fix missing disposition
    if "disposition" not in fixed:
        fixed["disposition"] = "HUMAN_REVIEW"

    # Fix missing urgency_level
    if "urgency_level" not in fixed:
        fixed["urgency_level"] = "HIGH"

    # Fix confidence_score
    cs = fixed.get("confidence_score")
    if cs is None:
        fixed["confidence_score"] = 0.0
    elif isinstance(cs, str):
        try:
            fixed["confidence_score"] = float(cs)
        except ValueError:
            fixed["confidence_score"] = 0.0

    # Fix escalation_required
    if "escalation_required" not in fixed:
        fixed["escalation_required"] = True  # Conservative default

    # Fix model_version
    if "model_version" not in fixed:
        fixed["model_version"] = "unknown"

    # Fix timestamp
    if "timestamp" not in fixed:
        fixed["timestamp"] = datetime.now(timezone.utc).isoformat()

    return fixed
