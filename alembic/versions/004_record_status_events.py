"""Dashboard intake-record status audit events (PR 4)

Revision ID: 004_record_status_events
Revises: 003_phase10_platform
Create Date: 2026-06-11
"""

from typing import Sequence, Union

from alembic import op  # type: ignore[attr-defined]
import sqlalchemy as sa  # type: ignore[import-untyped]

revision: str = "004_record_status_events"
down_revision: Union[str, None] = "003_phase10_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "record_status_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(now() at time zone 'utc')"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_record_status_events_session_id",
        "record_status_events",
        ["session_id"],
    )
    op.create_index(
        "ix_record_status_events_created_at",
        "record_status_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_record_status_events_created_at", table_name="record_status_events"
    )
    op.drop_index(
        "ix_record_status_events_session_id", table_name="record_status_events"
    )
    op.drop_table("record_status_events")
