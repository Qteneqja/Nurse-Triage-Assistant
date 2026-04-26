"""Phase 10 decision-support platform foundation

Revision ID: 003_phase10_platform
Revises: 002_phase4_hardening
Create Date: 2026-04-26
"""

from typing import Sequence, Union

from alembic import op  # type: ignore[attr-defined]
import sqlalchemy as sa  # type: ignore[import-untyped]
from sqlalchemy.dialects import postgresql  # type: ignore[import-untyped]

revision: str = "003_phase10_platform"
down_revision: Union[str, None] = "002_phase4_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
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
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index("ix_organizations_status", "organizations", ["status"])

    op.create_table(
        "verticals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
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
        sa.UniqueConstraint("key", name="uq_verticals_key"),
    )
    op.create_index("ix_verticals_status", "verticals", ["status"])

    op.create_table(
        "organization_workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", name="fk_org_workflows_organization_id"),
            nullable=False,
        ),
        sa.Column(
            "vertical_id",
            sa.String(36),
            sa.ForeignKey("verticals.id", name="fk_org_workflows_vertical_id"),
            nullable=False,
        ),
        sa.Column("workflow_id", sa.String(100), nullable=False),
        sa.Column("workflow_version", sa.String(50), nullable=False),
        sa.Column("is_default", sa.Boolean, server_default="true", nullable=False),
        sa.Column(
            "config_json",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
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
        sa.UniqueConstraint(
            "organization_id",
            "vertical_id",
            "workflow_id",
            name="uq_org_workflows_org_vertical_workflow",
        ),
    )
    op.create_index(
        "ix_organization_workflows_organization_id",
        "organization_workflows",
        ["organization_id"],
    )
    op.create_index(
        "ix_organization_workflows_vertical_id",
        "organization_workflows",
        ["vertical_id"],
    )
    op.create_index(
        "ix_organization_workflows_status",
        "organization_workflows",
        ["status"],
    )
    op.create_index(
        "ix_organization_workflows_workflow_id",
        "organization_workflows",
        ["workflow_id"],
    )
    op.create_index(
        "ix_org_workflows_org_vertical_default",
        "organization_workflows",
        ["organization_id", "vertical_id", "is_default"],
    )

    op.create_table(
        "phone_numbers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", name="fk_phone_numbers_organization_id"),
            nullable=False,
        ),
        sa.Column(
            "vertical_id",
            sa.String(36),
            sa.ForeignKey("verticals.id", name="fk_phone_numbers_vertical_id"),
            nullable=False,
        ),
        sa.Column("workflow_id", sa.String(100), nullable=False),
        sa.Column("e164_number", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(50), server_default="twilio", nullable=False),
        sa.Column("provider_sid", sa.String(100), nullable=True),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
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
    )
    op.create_index(
        "ix_phone_numbers_e164_number",
        "phone_numbers",
        ["e164_number"],
        unique=True,
    )
    op.create_index("ix_phone_numbers_organization_id", "phone_numbers", ["organization_id"])
    op.create_index("ix_phone_numbers_vertical_id", "phone_numbers", ["vertical_id"])
    op.create_index("ix_phone_numbers_workflow_id", "phone_numbers", ["workflow_id"])
    op.create_index("ix_phone_numbers_status", "phone_numbers", ["status"])

    op.add_column(
        "triage_sessions",
        sa.Column(
            "organization_id",
            sa.String(36),
            nullable=True,
        ),
    )
    op.add_column(
        "triage_sessions",
        sa.Column("vertical_key", sa.String(100), nullable=True),
    )
    op.add_column(
        "triage_sessions",
        sa.Column("workflow_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "triage_sessions",
        sa.Column("workflow_version", sa.String(50), nullable=True),
    )
    op.add_column(
        "triage_sessions",
        sa.Column(
            "phone_number_id",
            sa.String(36),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_triage_sessions_organization_id",
        "triage_sessions",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_triage_sessions_phone_number_id",
        "triage_sessions",
        "phone_numbers",
        ["phone_number_id"],
        ["id"],
    )
    op.create_index("ix_triage_sessions_organization_id", "triage_sessions", ["organization_id"])
    op.create_index("ix_triage_sessions_workflow_id", "triage_sessions", ["workflow_id"])
    op.create_index("ix_triage_sessions_phone_number_id", "triage_sessions", ["phone_number_id"])

    op.create_table(
        "conversation_extractions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey(
                "triage_sessions.session_id",
                ondelete="CASCADE",
                name="fk_conversation_extractions_session_id",
            ),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey(
                "organizations.id", name="fk_conversation_extractions_organization_id"
            ),
            nullable=True,
        ),
        sa.Column("vertical_key", sa.String(100), nullable=False),
        sa.Column("workflow_id", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.String(100), nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column(
            "entities_json",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metrics_json",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "flags_json",
            _jsonb(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "recommended_actions_json",
            _jsonb(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence_score", sa.Float, nullable=True),
        sa.Column("raw_output_json", _jsonb(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(now() at time zone 'utc')"),
        ),
    )
    op.create_index(
        "ix_conversation_extractions_session_id",
        "conversation_extractions",
        ["session_id"],
    )
    op.create_index(
        "ix_conversation_extractions_organization_id",
        "conversation_extractions",
        ["organization_id"],
    )
    op.create_index(
        "ix_conversation_extractions_workflow_id",
        "conversation_extractions",
        ["workflow_id"],
    )
    op.create_index(
        "ix_conversation_extractions_created_at",
        "conversation_extractions",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("conversation_extractions")

    op.drop_index("ix_triage_sessions_phone_number_id", table_name="triage_sessions")
    op.drop_index("ix_triage_sessions_workflow_id", table_name="triage_sessions")
    op.drop_index("ix_triage_sessions_organization_id", table_name="triage_sessions")
    op.drop_constraint(
        "fk_triage_sessions_phone_number_id",
        "triage_sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_triage_sessions_organization_id",
        "triage_sessions",
        type_="foreignkey",
    )
    op.drop_column("triage_sessions", "phone_number_id")
    op.drop_column("triage_sessions", "workflow_version")
    op.drop_column("triage_sessions", "workflow_id")
    op.drop_column("triage_sessions", "vertical_key")
    op.drop_column("triage_sessions", "organization_id")

    op.drop_table("phone_numbers")
    op.drop_table("organization_workflows")
    op.drop_table("verticals")
    op.drop_table("organizations")
