"""Typed admin/dashboard schemas for Phase 12."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ActionStatus(str, Enum):
    """Internal lifecycle for placeholder post-call actions."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"


class OrganizationListItem(BaseModel):
    """Organization row shown in the admin shell."""

    organization_id: str
    name: str
    slug: str | None = None
    status: str = "active"
    verticals: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    session_count: int = 0


class WorkflowListItem(BaseModel):
    """Registered workflow row shown in the admin shell."""

    workflow_id: str
    display_name: str
    vertical: str
    version: str
    required_fields: list[str] = Field(default_factory=list)
    supported_output_types: list[str] = Field(default_factory=list)
    default_output_type: str | None = None
    supports_post_call_extraction: bool = False
    session_count: int = 0


class TurnDetail(BaseModel):
    """Transcript/decision details for a single session turn."""

    turn_index: int | None = None
    timestamp: datetime | None = None
    role: str | None = None
    caller_text: str | None = None
    assistant_text: str | None = None
    text: str | None = None
    red_flags_triggered: list[Any] = Field(default_factory=list)
    rules_triggered: list[Any] = Field(default_factory=list)
    protocol_hits: list[Any] = Field(default_factory=list)
    protocol_citations: list[Any] = Field(default_factory=list)
    confidence_score: float | None = None
    disposition: str | None = None
    escalation_required: bool | None = None
    finalization_reason: str | None = None


class AuditMetadata(BaseModel):
    """Cross-vertical audit fields surfaced for operators."""

    finalization_reason: str | None = None
    healthcare_intake_completeness: dict[str, Any] | None = None
    healthcare_finalization_blocked_reason: str | None = None
    rules_triggered: list[Any] = Field(default_factory=list)
    red_flags_triggered: list[Any] = Field(default_factory=list)
    confidence_score: float | None = None
    escalation_required: bool = False
    sbar_available: bool = False
    turn_count: int = 0
    max_turns: int | None = None
    audit_trace: dict[str, Any] | None = None
    workflow_audit_metadata: dict[str, Any] = Field(default_factory=dict)
    safety_events: list[Any] = Field(default_factory=list)


class ProposedAction(BaseModel):
    """Deterministic internal placeholder action.

    Phase 12 does not execute external actions. These records are only a
    typed approval surface for future sandboxed automation.
    """

    action_id: str
    session_id: str
    organization_id: str | None = None
    vertical: str | None = None
    workflow_id: str | None = None
    action_type: str
    title: str
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: ActionStatus = ActionStatus.PROPOSED
    source: Literal["phase12_placeholder"] = "phase12_placeholder"
    execution_attempted: bool = False
    external_action_executed: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    status_reason: str | None = None


class SessionListItem(BaseModel):
    """Session row shown in the admin shell."""

    session_id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    organization_id: str | None = None
    organization_name: str | None = None
    vertical: str | None = None
    workflow_id: str | None = None
    workflow_version: str | None = None
    disposition: str | None = None
    confidence_score: float | None = None
    escalation_required: bool = False
    status: str = "active"
    finalization_reason: str | None = None
    proposed_action_count: int = 0


class SessionDetail(BaseModel):
    """Full admin session detail view."""

    session: SessionListItem
    call_metadata: dict[str, Any] = Field(default_factory=dict)
    turns: list[TurnDetail] = Field(default_factory=list)
    transcript: list[TurnDetail] = Field(default_factory=list)
    final_output: dict[str, Any] | None = None
    audit_metadata: AuditMetadata
    safety_events: list[Any] = Field(default_factory=list)
    healthcare_metadata: dict[str, Any] | None = None
    property_management_metadata: dict[str, Any] | None = None
    proposed_actions: list[ProposedAction] = Field(default_factory=list)


class DashboardSummary(BaseModel):
    """Top-level dashboard metrics."""

    total_sessions: int
    escalations: int
    human_reviews: int
    sessions_by_vertical: dict[str, int] = Field(default_factory=dict)
    pending_actions: int
    completed_actions: int
    recent_sessions: list[SessionListItem] = Field(default_factory=list)
