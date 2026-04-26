from sqlalchemy import create_engine, inspect

from src.storage.models import Base, TriageSessionModel


def test_phase10_tables_created_by_models():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)

    tables = set(inspect(engine).get_table_names())

    assert "organizations" in tables
    assert "verticals" in tables
    assert "organization_workflows" in tables
    assert "phone_numbers" in tables
    assert "conversation_extractions" in tables


def test_triage_sessions_has_nullable_platform_columns():
    columns = {column.name for column in TriageSessionModel.__table__.columns}

    assert {
        "organization_id",
        "vertical_key",
        "workflow_id",
        "workflow_version",
        "phone_number_id",
    }.issubset(columns)


def test_phase10_required_indexes_exist():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    triage_indexes = {idx["name"] for idx in inspector.get_indexes("triage_sessions")}
    phone_indexes = {idx["name"] for idx in inspector.get_indexes("phone_numbers")}
    workflow_indexes = {
        idx["name"] for idx in inspector.get_indexes("organization_workflows")
    }
    extraction_indexes = {
        idx["name"] for idx in inspector.get_indexes("conversation_extractions")
    }

    assert "ix_triage_sessions_organization_id" in triage_indexes
    assert "ix_triage_sessions_workflow_id" in triage_indexes
    assert "ix_phone_numbers_e164_number" in phone_indexes
    assert "ix_organization_workflows_organization_id" in workflow_indexes
    assert "ix_organization_workflows_vertical_id" in workflow_indexes
    assert "ix_conversation_extractions_session_id" in extraction_indexes
    assert "ix_conversation_extractions_organization_id" in extraction_indexes


def test_phase10_json_defaults_are_not_shared_between_rows():
    from src.storage.models import ConversationExtractionModel

    first_default = ConversationExtractionModel.__table__.c.entities_json.default
    second_default = ConversationExtractionModel.__table__.c.metrics_json.default

    assert callable(first_default.arg)
    assert callable(second_default.arg)
