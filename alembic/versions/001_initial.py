"""Initial Phase 3 schema: triage_sessions and triage_turns

Revision ID: 001_initial
Revises: None
Create Date: 2026-02-18
"""
from typing import Sequence, Union

from alembic import op  # type: ignore[attr-defined]
import sqlalchemy as sa  # type: ignore[import-untyped]

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # triage_sessions table
    op.create_table(
        "triage_sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("channel", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(now() at time zone 'utc')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(now() at time zone 'utc')"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("final_disposition", sa.String(50), nullable=True),
        sa.Column("escalation_reason", sa.Text, nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("model_version", sa.String(50), nullable=True),
        sa.Column("protocol_version_used", sa.String(50), nullable=True),
        sa.Column("metadata", sa.JSON, nullable=True),
    )
    op.create_index("ix_triage_sessions_created_at", "triage_sessions", ["created_at"])

    # triage_turns table
    op.create_table(
        "triage_turns",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer, nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("(now() at time zone 'utc')"),
        ),
        sa.Column("user_text", sa.Text, nullable=True),
        sa.Column("system_text", sa.Text, nullable=True),
        sa.Column("extracted_entities", sa.JSON, nullable=True),
        sa.Column("red_flags_triggered", sa.JSON, nullable=True),
        sa.Column("rules_triggered", sa.JSON, nullable=True),
        sa.Column("protocol_hits", sa.JSON, nullable=True),
        sa.Column("protocol_citations", sa.JSON, nullable=True),
        sa.Column("confidence_score", sa.Float, nullable=True),
        sa.Column("confidence_breakdown", sa.JSON, nullable=True),
        sa.Column("disposition", sa.String(50), nullable=True),
        sa.Column("next_action", sa.String(50), nullable=True),
        sa.Column("escalation_required", sa.Boolean, server_default="false"),
        sa.Column("safety_events", sa.JSON, nullable=True),
    )
    op.create_index("ix_triage_turns_session_id", "triage_turns", ["session_id"])
    op.create_index(
        "ix_session_turn",
        "triage_turns",
        ["session_id", "turn_index"],
        unique=True,
    )
    op.create_index(
        "ix_triage_turns_escalation_required",
        "triage_turns",
        ["escalation_required"],
    )


def downgrade() -> None:
    op.drop_table("triage_turns")
    op.drop_table("triage_sessions")
