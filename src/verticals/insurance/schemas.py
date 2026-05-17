"""Schemas for insurance FNOL claims intake."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


InsuranceClaimDisposition = Literal[
    "EMERGENCY_SERVICES_NOW",
    "URGENT_ADJUSTER_REVIEW",
    "STANDARD_CLAIM_INTAKE",
    "DOCUMENTS_NEEDED",
    "INFORMATION_ONLY",
    "HUMAN_REVIEW",
]


class InsuranceClaimIntake(BaseModel):
    """Structured fields collected during scripted FNOL intake."""

    caller_name: str | None = None
    callback_number: str | None = None
    policy_number: str | None = None
    claim_type: str | None = None
    loss_datetime: str | None = None
    loss_location: str | None = None
    incident_summary: str | None = None


class InsuranceClaimAssessment(BaseModel):
    """Deterministic FNOL routing assessment."""

    disposition: InsuranceClaimDisposition
    routing_reason: str
    recommended_action: str
    relationship_to_policyholder: str | None = None
    preferred_callback_method: str = "phone"
    emergency_or_safety_issue: bool = False
    injuries_mentioned: bool = False
    emergency_services_involved: bool = False
    police_or_fire_report: bool = False
    property_secure: bool | None = None
    mitigation_needed: bool = False
    documents_available: bool = False
    missing_information: list[str] = Field(default_factory=list)
    rules_triggered: list[str] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    human_review_required: bool = False
    disclaimers_given: list[str] = Field(default_factory=list)


class InsuranceClaimRecord(BaseModel):
    """Final structured FNOL claim intake payload."""

    workflow_id: str
    vertical: str
    caller_name: str | None = None
    callback_number: str | None = None
    policy_number: str | None = None
    claim_type: str | None = None
    loss_datetime: str | None = None
    loss_location: str | None = None
    incident_summary: str | None = None
    relationship_to_policyholder: str | None = None
    preferred_callback_method: str = "phone"
    emergency_or_safety_issue: bool = False
    injuries_mentioned: bool = False
    emergency_services_involved: bool = False
    police_or_fire_report: bool = False
    property_secure: bool | None = None
    mitigation_needed: bool = False
    documents_available: bool = False
    missing_information: list[str] = Field(default_factory=list)
    recommended_routing: InsuranceClaimDisposition
    confidence: float = Field(ge=0.0, le=1.0)
    human_review_required: bool = False
    disclaimers_given: list[str] = Field(default_factory=list)
    routing_reason: str
    recommended_action: str
    rules_triggered: list[str] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
