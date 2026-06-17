"""Schemas for the minimal Birchwood collision-intake workflow."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CollisionMinDisposition = Literal[
    "READY_FOR_SPECIALIST",
    "CALLBACK_NEEDED",
]

DrivableStatus = Literal["drivable", "not_drivable", "unknown"]


class CollisionMinIntake(BaseModel):
    """Only what a collision specialist needs to start the file."""

    caller_name: str | None = None
    callback_number: str | None = None
    vehicle_year: int | None = None
    vehicle_year_raw: str | None = None
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    vehicle_color: str | None = None
    license_plate: str | None = None
    vin: str | None = None
    damage_description: str | None = None
    drivable_status: DrivableStatus | None = None
    drivable_raw: str | None = None
    vehicle_location: str | None = None
    # MPI claim status captured purely as data - no coverage/claim decision.
    mpi_claim_opened: bool | None = None
    mpi_claim_raw: str | None = None
    mpi_claim_number: str | None = None


class CollisionMinAssessment(BaseModel):
    """Deterministic intake-completeness result (no triage/decision)."""

    disposition: CollisionMinDisposition
    handoff_mode: Literal["warm_transfer", "callback"]
    missing_information: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    rules_triggered: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    disclaimers_given: list[str] = Field(default_factory=list)


class CollisionMinRecord(BaseModel):
    """Final structured intake payload handed to the collision specialist."""

    workflow_id: str
    vertical: str
    powered_by: str
    client_target: str
    status: str
    caller_name: str | None = None
    callback_number: str | None = None
    vehicle_year: int | None = None
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    vehicle_color: str | None = None
    license_plate: str | None = None
    vin: str | None = None
    damage_description: str | None = None
    drivable_status: DrivableStatus | None = None
    vehicle_location: str | None = None
    mpi_claim_opened: bool | None = None
    mpi_claim_number: str | None = None
    flags: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommended_routing: CollisionMinDisposition
    handoff_mode: Literal["warm_transfer", "callback"]
    handoff_summary: str = ""
    disclaimers_given: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    human_review_required: bool = False
