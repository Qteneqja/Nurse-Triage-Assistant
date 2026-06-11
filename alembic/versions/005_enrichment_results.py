"""Post-call enrichment results (shadow mode)

Revision ID: 005_enrichment_results
Revises: 004_record_status_events
Create Date: 2026-06-11
"""

from typing import Sequence, Union

from alembic import op  # type: ignore[attr-defined]
import sqlalchemy as sa  # type: ignore[import-untyped]

revision: str = "005_enrichment_results"
down_revision: Union[str, None] = "004_record_status_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "enrichment_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("triage_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_sid", sa.String(64), nullable=True),
        sa.Column("feature", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("pii_mode", sa.String(10), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("tokens_map_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(now() at time zone 'utc')"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_enrichment_results_session_id", "enrichment_results", ["session_id"]
    )
    op.create_index(
        "ix_enrichment_results_call_sid", "enrichment_results", ["call_sid"]
    )
    op.create_index(
        "ix_enrichment_results_created_at", "enrichment_results", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_enrichment_results_created_at", table_name="enrichment_results")
    op.drop_index("ix_enrichment_results_call_sid", table_name="enrichment_results")
    op.drop_index("ix_enrichment_results_session_id", table_name="enrichment_results")
    op.drop_table("enrichment_results")
