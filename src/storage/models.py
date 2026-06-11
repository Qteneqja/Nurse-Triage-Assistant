"""
SQLAlchemy 2.0 Declarative Models — Phase 4 Hardened

Defines the persistent schema for:
- triage_sessions   (master session record)
- triage_turns      (per-turn clinical data)
- messages          (conversation audit log)
- decisions         (per-turn safety gate decisions)
- safety_events     (diagnosis rewrites, red-flag overrides, schema fallbacks)
- rule_triggers     (individual rule fire records)

All tables support:
- Foreign keys to triage_sessions
- Indexes on session_id, created_at
- Soft-delete via deleted_at column
- PHI masking flag (phi_masked)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    ForeignKey,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base."""

    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# triage_sessions
# ---------------------------------------------------------------------------


class TriageSessionModel(Base):
    """Persistent triage session record."""

    __tablename__ = "triage_sessions"

    session_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    caller_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finalized_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active"
    )  # active | ended | escalated
    final_disposition: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    escalation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    protocol_version_used: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    phi_masked: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSON, nullable=True
    )
    organization_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=True, index=True
    )
    vertical_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    workflow_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True
    )
    workflow_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    phone_number_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("phone_numbers.id"), nullable=True, index=True
    )
    # Soft delete
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    turns: Mapped[list["TriageTurnModel"]] = relationship(
        "TriageTurnModel",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="TriageTurnModel.turn_index",
    )
    messages: Mapped[list["MessageModel"]] = relationship(
        "MessageModel",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MessageModel.turn_index",
    )
    decisions: Mapped[list["DecisionModel"]] = relationship(
        "DecisionModel",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="DecisionModel.turn_index",
    )
    safety_events: Mapped[list["SafetyEventModel"]] = relationship(
        "SafetyEventModel",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    rule_triggers: Mapped[list["RuleTriggerModel"]] = relationship(
        "RuleTriggerModel",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    extractions: Mapped[list["ConversationExtractionModel"]] = relationship(
        "ConversationExtractionModel",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<TriageSession {self.session_id} status={self.status}>"


# ---------------------------------------------------------------------------
# triage_turns (per-turn clinical data — existing table, enhanced)
# ---------------------------------------------------------------------------


class TriageTurnModel(Base):
    """Persistent per-turn triage record."""

    __tablename__ = "triage_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    # PHI-controlled fields (only stored when STORE_PHI=true)
    user_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    system_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Structured clinical data
    extracted_entities: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    red_flags_triggered: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    rules_triggered: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    protocol_hits: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    protocol_citations: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Scores
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_breakdown: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Disposition
    disposition: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    next_action: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    escalation_required: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True
    )

    # Safety events
    safety_events: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # PHI masking flag
    phi_masked: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationship
    session: Mapped["TriageSessionModel"] = relationship(
        "TriageSessionModel", back_populates="turns"
    )

    __table_args__ = (
        UniqueConstraint("session_id", "turn_index", name="uq_session_turn"),
        Index("ix_session_turn", "session_id", "turn_index", unique=True),
    )

    def __repr__(self) -> str:
        return f"<TriageTurn session={self.session_id} turn={self.turn_index}>"


# ---------------------------------------------------------------------------
# messages (conversation audit log)
# ---------------------------------------------------------------------------


class MessageModel(Base):
    """Conversation message record for full audit trail."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # caller | assistant | system
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    phi_masked: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    session: Mapped["TriageSessionModel"] = relationship(
        "TriageSessionModel", back_populates="messages"
    )

    def __repr__(self) -> str:
        return f"<Message session={self.session_id} turn={self.turn_index} role={self.role}>"


# ---------------------------------------------------------------------------
# decisions (per-turn safety gate decision record)
# ---------------------------------------------------------------------------


class DecisionModel(Base):
    """Per-turn decision record from the centralized safety gate."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    disposition: Mapped[str] = mapped_column(String(50), nullable=False)
    urgency_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    escalation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    rules_triggered: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    red_flags_triggered: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    protocol_references: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    protocol_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    gate_trace: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    diagnosis_rewrites: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    safe_fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    finalized_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    session: Mapped["TriageSessionModel"] = relationship(
        "TriageSessionModel", back_populates="decisions"
    )

    def __repr__(self) -> str:
        return f"<Decision session={self.session_id} turn={self.turn_index} disp={self.disposition}>"


# ---------------------------------------------------------------------------
# safety_events (diagnosis rewrites, red-flag overrides, schema fallbacks)
# ---------------------------------------------------------------------------


class SafetyEventModel(Base):
    """Safety event record for audit trail."""

    __tablename__ = "safety_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # diagnosis_rewrite | red_flag_override | schema_fallback | post_check_violation
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    # CRITICAL | HIGH | MEDIUM | LOW
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    rule_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    session: Mapped["TriageSessionModel"] = relationship(
        "TriageSessionModel", back_populates="safety_events"
    )

    def __repr__(self) -> str:
        return f"<SafetyEvent session={self.session_id} type={self.event_type}>"


# ---------------------------------------------------------------------------
# rule_triggers (individual rule fire records)
# ---------------------------------------------------------------------------


class RuleTriggerModel(Base):
    """Individual rule trigger record for audit trail."""

    __tablename__ = "rule_triggers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    turn_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    rule_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    forced_disposition: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    weight: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    session: Mapped["TriageSessionModel"] = relationship(
        "TriageSessionModel", back_populates="rule_triggers"
    )

    def __repr__(self) -> str:
        return f"<RuleTrigger session={self.session_id} rule={self.rule_id}>"


# ---------------------------------------------------------------------------
# Phase 10 SaaS routing foundation
# ---------------------------------------------------------------------------


class OrganizationModel(Base):
    """Tenant organization."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    workflows: Mapped[list["OrganizationWorkflowModel"]] = relationship(
        "OrganizationWorkflowModel",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    phone_numbers: Mapped[list["PhoneNumberModel"]] = relationship(
        "PhoneNumberModel",
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class VerticalModel(Base):
    """Supported product vertical."""

    __tablename__ = "verticals"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    workflows: Mapped[list["OrganizationWorkflowModel"]] = relationship(
        "OrganizationWorkflowModel",
        back_populates="vertical",
    )
    phone_numbers: Mapped[list["PhoneNumberModel"]] = relationship(
        "PhoneNumberModel",
        back_populates="vertical",
    )


class OrganizationWorkflowModel(Base):
    """Workflow enabled for an organization and vertical."""

    __tablename__ = "organization_workflows"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False, index=True
    )
    vertical_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("verticals.id"), nullable=False, index=True
    )
    workflow_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    workflow_version: Mapped[str] = mapped_column(String(50), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    organization: Mapped["OrganizationModel"] = relationship(
        "OrganizationModel", back_populates="workflows"
    )
    vertical: Mapped["VerticalModel"] = relationship(
        "VerticalModel", back_populates="workflows"
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "vertical_id",
            "workflow_id",
            name="uq_org_workflows_org_vertical_workflow",
        ),
        Index(
            "ix_org_workflows_org_vertical_default",
            "organization_id",
            "vertical_id",
            "is_default",
        ),
    )


class PhoneNumberModel(Base):
    """Inbound provider phone number mapped to a workflow."""

    __tablename__ = "phone_numbers"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False, index=True
    )
    vertical_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("verticals.id"), nullable=False, index=True
    )
    workflow_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    e164_number: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), default="twilio")
    provider_sid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    organization: Mapped["OrganizationModel"] = relationship(
        "OrganizationModel", back_populates="phone_numbers"
    )
    vertical: Mapped["VerticalModel"] = relationship(
        "VerticalModel", back_populates="phone_numbers"
    )


class ConversationExtractionModel(Base):
    """Post-call structured extraction result."""

    __tablename__ = "conversation_extractions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=True, index=True
    )
    vertical_key: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entities_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    flags_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recommended_actions_json: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_output_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    session: Mapped["TriageSessionModel"] = relationship(
        "TriageSessionModel", back_populates="extractions"
    )

    def __repr__(self) -> str:
        return f"<ConversationExtraction session={self.session_id} workflow={self.workflow_id}>"


class RecordStatusEventModel(Base):
    """Dashboard intake-record status change (PR 4) — immutable audit event.

    The record's current status is the newest event for the session;
    sessions with no events derive a default ('escalated' for injury/urgent
    records, otherwise 'new') in the dashboard layer.
    """

    __tablename__ = "record_status_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    def __repr__(self) -> str:
        return (
            f"<RecordStatusEvent session={self.session_id} "
            f"status={self.status} actor={self.actor}>"
        )


class EnrichmentResultModel(Base):
    """Post-call enrichment output (shadow mode) — separate from the record.

    One row per (session, feature) run. The source call record is never
    modified by enrichment; deleting this table loses only enrichment.
    """

    __tablename__ = "enrichment_results"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    call_sid: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    pii_mode: Mapped[str] = mapped_column(String(10), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    tokens_map_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    def __repr__(self) -> str:
        return (
            f"<EnrichmentResult session={self.session_id} "
            f"feature={self.feature} status={self.status}>"
        )
