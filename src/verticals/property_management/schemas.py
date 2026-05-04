"""Schemas for property management maintenance intake."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


MaintenanceDisposition = Literal[
    "EMERGENCY",
    "SAME_DAY",
    "SCHEDULED_REPAIR",
    "INFORMATION_ONLY",
    "HUMAN_REVIEW",
]


class MaintenanceIntake(BaseModel):
    """Structured fields collected during scripted tenant intake."""

    caller_name: str | None = None
    property_address: str | None = None
    unit_number: str | None = None
    issue_type: str | None = None
    issue_description: str | None = None
    access_permission: str | None = None
    callback_phone: str | None = None


class MaintenanceClassification(BaseModel):
    """Deterministic urgency classification."""

    disposition: MaintenanceDisposition
    urgency_reason: str
    recommended_action: str
    vendor_type: str
    safety_flags: list[str] = Field(default_factory=list)
    rules_triggered: list[str] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0)


class MaintenanceWorkOrder(BaseModel):
    """Final structured work order payload."""

    caller_name: str | None
    property_address: str | None
    unit_number: str | None
    issue_type: str | None
    issue_description: str | None
    access_permission: str | None
    callback_phone: str | None
    disposition: MaintenanceDisposition
    urgency_reason: str
    recommended_action: str
    vendor_type: str
    safety_flags: list[str] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0)
