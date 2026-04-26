"""Healthcare vertical schema helpers."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.verticals.healthcare.constants import HEALTHCARE_DISPOSITIONS


class HealthcareExtractionSchema(BaseModel):
    """Baseline analytics fields extracted after a healthcare triage call."""

    schema_version: str = "healthcare_triage_extraction_v1"
    fields: list[str] = Field(
        default_factory=lambda: [
            "chief_complaint",
            "age",
            "sex",
            "final_disposition",
            "confidence_score",
            "red_flags_triggered",
            "rules_triggered",
            "protocols_used",
            "escalation_required",
            "sbar_available",
            "call_duration",
        ]
    )
    dispositions: list[str] = Field(default_factory=lambda: HEALTHCARE_DISPOSITIONS)

