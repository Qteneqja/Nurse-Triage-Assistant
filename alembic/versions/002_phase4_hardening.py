"""Phase 4 hardening: audit log tables, safety_events, rule_triggers, PHI masking

Revision ID: 002_phase4_hardening
Revises: 001_initial
Create Date: 2026-02-19
"""
from typing import Sequence, Union

from alembic import op  # type: ignore[attr-defined]
import sqlalchemy as sa  # type: ignore[import-untyped]

revision: str = "002_phase4_hardening"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Add columns to triage_sessions ───────────────────────────────────
    op.add_column("triage_sessions", sa.Column(
        "caller_id", sa.String(100), nullable=True,
    ))
    op.add_column("triage_sessions", sa.Column(
        "phi_masked", sa.Boolean, server_default="true", nullable=False,
    ))
    op.add_column("triage_sessions", sa.Column(
        "deleted_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("triage_sessions", sa.Column(
        "confidence_score", sa.Float, nullable=True,
    ))
    op.add_column("triage_sessions", sa.Column(
        "finalized_at", sa.DateTime(timezone=True), nullable=True,
    ))

    # ── Add columns to triage_turns ──────────────────────────────────────
    op.add_column("triage_turns", sa.Column(
        "phi_masked", sa.Boolean, server_default="true", nullable=False,
    ))

    # ── messages table ───────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id", sa.String(36),
            sa.ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer, nullable=False),
        sa.Column("role", sa.String(20), nullable=False),  # caller | assistant | system
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("phi_masked", sa.Boolean, server_default="true", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("(now() at time zone 'utc')"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])

    # ── decisions table ──────────────────────────────────────────────────
    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id", sa.String(36),
            sa.ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer, nullable=False),
        sa.Column("disposition", sa.String(50), nullable=False),
        sa.Column("urgency_level", sa.String(20), nullable=True),
        sa.Column("confidence_score", sa.Float, nullable=True),
        sa.Column("escalation_required", sa.Boolean, server_default="false"),
        sa.Column("rules_triggered", sa.JSON, nullable=True),
        sa.Column("red_flags_triggered", sa.JSON, nullable=True),
        sa.Column("protocol_references", sa.JSON, nullable=True),
        sa.Column("protocol_version", sa.String(50), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("model_version", sa.String(50), nullable=True),
        sa.Column("gate_trace", sa.JSON, nullable=True),
        sa.Column("diagnosis_rewrites", sa.JSON, nullable=True),
        sa.Column("safe_fallback_used", sa.Boolean, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("(now() at time zone 'utc')"),
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_decisions_session_id", "decisions", ["session_id"])
    op.create_index("ix_decisions_created_at", "decisions", ["created_at"])

    # ── safety_events table ──────────────────────────────────────────────
    op.create_table(
        "safety_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id", sa.String(36),
            sa.ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        # diagnosis_rewrite | red_flag_override | schema_fallback | post_check_violation
        sa.Column("severity", sa.String(20), nullable=False),
        # CRITICAL | HIGH | MEDIUM | LOW
        sa.Column("details", sa.JSON, nullable=True),
        sa.Column("rule_id", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("(now() at time zone 'utc')"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_safety_events_session_id", "safety_events", ["session_id"])
    op.create_index("ix_safety_events_created_at", "safety_events", ["created_at"])

    # ── rule_triggers table ──────────────────────────────────────────────
    op.create_table(
        "rule_triggers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id", sa.String(36),
            sa.ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer, nullable=True),
        sa.Column("rule_id", sa.String(100), nullable=False),
        sa.Column("rule_description", sa.Text, nullable=True),
        sa.Column("forced_disposition", sa.String(50), nullable=True),
        sa.Column("weight", sa.Integer, nullable=True),
        sa.Column("critical", sa.Boolean, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("(now() at time zone 'utc')"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rule_triggers_session_id", "rule_triggers", ["session_id"])
    op.create_index("ix_rule_triggers_created_at", "rule_triggers", ["created_at"])
    op.create_index("ix_rule_triggers_rule_id", "rule_triggers", ["rule_id"])


def downgrade() -> None:
    op.drop_table("rule_triggers")
    op.drop_table("safety_events")
    op.drop_table("decisions")
    op.drop_table("messages")

    op.drop_column("triage_turns", "phi_masked")

    op.drop_column("triage_sessions", "finalized_at")
    op.drop_column("triage_sessions", "confidence_score")
    op.drop_column("triage_sessions", "deleted_at")
    op.drop_column("triage_sessions", "phi_masked")
    op.drop_column("triage_sessions", "caller_id")
