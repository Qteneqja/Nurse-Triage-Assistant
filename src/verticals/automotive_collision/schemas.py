"""Schemas for ORCA's automotive collision intake workflow."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


AutomotiveCollisionOutcome = Literal[
    "COMPLETED_INTAKE",
    "INCOMPLETE_CALLBACK_NEEDED",
    "TRANSFER_COLLISION_CENTER",
    "TRANSFER_GLASS_DEPARTMENT",
    "DECLINED_VEHICLE_YEAR",
    "DECLINED_REBUILT_SALVAGE",
    "HUMAN_REVIEW",
]

AutomotiveCollisionStatus = Literal[
    "completed",
    "incomplete",
    "transferred",
    "declined",
    "human_review",
]


class AutomotiveCollisionIntake(BaseModel):
    """Structured fields collected during Birchwood collision intake."""

    caller_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    vehicle_year: int | None = None
    vehicle_year_raw: str | None = None
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    license_plate: str | None = None
    is_drivable: bool | None = None
    drivable_raw: str | None = None
    damage_type: str | None = None
    glass_only: bool | None = None
    body_damage: bool | None = None
    incident_description: str | None = None
    incident_datetime: str | None = None
    incident_location: str | None = None
    # Invariant 3: "reported" | "denied" | None (unknown — must be asked).
    injuries_state: str | None = None
    other_parties: str | None = None
    police_report_filed: str | None = None
    photos_available: str | None = None
    filing_insurance_claim: bool | None = None
    insurance_claim_raw: str | None = None
    insurance_provider: str | None = None
    claim_number: str | None = None
    preferred_collision_center: str | None = None
    preferred_timing: str | None = None
    urgency: str | None = None
    confirmation_ack: str | None = None
    correction_note: str | None = None
    is_rebuilt_or_salvage: bool | None = None
    rebuilt_salvage_raw: str | None = None
    already_spoke_to_someone: bool = False
    multiple_vehicles: bool = False
    caller_requested_transfer: bool = False


class AutomotiveCollisionAssessment(BaseModel):
    """Deterministic gate and routing result."""

    outcome: AutomotiveCollisionOutcome
    status: AutomotiveCollisionStatus
    result: str
    reason: str
    recommended_routing: AutomotiveCollisionOutcome
    transfer_department: str | None = None
    decline_reason: str | None = None
    preferred_collision_center: str | None = None
    available_collision_centers: list[str] = Field(default_factory=list)
    is_luxury: bool = False
    is_vw: bool = False
    private_pay: bool = False
    callback_needed: bool = False
    missing_information: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    rules_triggered: list[str] = Field(default_factory=list)
    disclaimers_given: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    human_review_required: bool = False


class AutomotiveCollisionRecord(BaseModel):
    """Final structured collision intake payload for staff review."""

    intake_id: str
    timestamp: str
    workflow_id: str
    vertical: str
    powered_by: str
    client_target: str
    workflow_status: str
    status: AutomotiveCollisionStatus
    customer: dict
    vehicle: dict
    incident: dict
    insurance: dict
    location: dict
    flags: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    conversation_transcript: str = ""
    outcome: dict
    caller_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    vehicle_year: int | None = None
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    license_plate: str | None = None
    is_drivable: bool | None = None
    damage_type: str | None = None
    glass_only: bool = False
    body_damage: bool = False
    incident_description: str | None = None
    incident_datetime: str | None = None
    incident_location: str | None = None
    injuries_state: str | None = None
    other_parties: str | None = None
    police_report_filed: str | None = None
    photos_available: str | None = None
    filing_insurance_claim: bool | None = None
    insurance_provider: str | None = None
    claim_number: str | None = None
    private_pay: bool = False
    preferred_collision_center: str | None = None
    preferred_timing: str | None = None
    urgency: str | None = None
    confirmation_ack: str | None = None
    correction_note: str | None = None
    available_collision_centers: list[str] = Field(default_factory=list)
    is_luxury: bool = False
    is_vw: bool = False
    is_rebuilt_or_salvage: bool | None = None
    recommended_routing: str
    transfer_department: str | None = None
    decline_reason: str | None = None
    callback_needed: bool = False
    transcript_summary: str = ""
    # PR 2 outputs: plain-language summary for the caller flow and the
    # shop-facing handoff (the Birchwood analogue of SBAR).
    plain_summary: str = ""
    shop_summary: str = ""
    disclaimers_given: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    human_review_required: bool = False
