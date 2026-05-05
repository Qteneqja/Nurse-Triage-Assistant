"""
Phase 3 — Storage Tests

Tests for PostgresStorage CRUD operations, PHI control, and migration.
Uses SQLite in-memory as a portable test backend.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage.models import Base, TriageSessionModel, TriageTurnModel
from src.orchestrator.schemas import (
    DecisionTraceEntry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_engine():
    """Create an in-memory SQLite engine with tables."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(sqlite_engine):
    """Create a SQLAlchemy session for testing."""
    Session = sessionmaker(bind=sqlite_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def postgres_storage(sqlite_engine):
    """Create a PostgresStorage backed by SQLite for testing."""
    from src.storage.postgres import PostgresStorage

    storage = PostgresStorage.__new__(PostgresStorage)
    storage._engine = sqlite_engine
    storage._SessionFactory = sessionmaker(bind=sqlite_engine)
    return storage


# ---------------------------------------------------------------------------
# Database Model Tests
# ---------------------------------------------------------------------------


class TestTriageSessionModel:
    """Test SQLAlchemy model for triage_sessions."""

    def test_create_session_record(self, db_session):
        """Session record can be created with all fields."""
        record = TriageSessionModel(
            session_id="test-session-001",
            channel="api",
            status="active",
            model_name="deepseek-chat",
            model_version="1.0",
            protocol_version_used="v1",
            metadata_json={"key": "value"},
        )
        db_session.add(record)
        db_session.commit()

        loaded = db_session.get(TriageSessionModel, "test-session-001")
        assert loaded is not None
        assert loaded.session_id == "test-session-001"
        assert loaded.channel == "api"
        assert loaded.status == "active"
        assert loaded.metadata_json == {"key": "value"}

    def test_session_defaults(self, db_session):
        """Default values are set correctly."""
        record = TriageSessionModel(session_id="test-defaults")
        db_session.add(record)
        db_session.commit()

        loaded = db_session.get(TriageSessionModel, "test-defaults")
        assert loaded.status == "active"
        assert loaded.ended_at is None
        assert loaded.final_disposition is None

    def test_session_lifecycle(self, db_session):
        """Session can transition through active → ended."""
        record = TriageSessionModel(session_id="lifecycle-test", status="active")
        db_session.add(record)
        db_session.commit()

        record.status = "ended"
        record.ended_at = datetime.now(timezone.utc)
        record.final_disposition = "ROUTINE"
        db_session.commit()

        loaded = db_session.get(TriageSessionModel, "lifecycle-test")
        assert loaded.status == "ended"
        assert loaded.final_disposition == "ROUTINE"
        assert loaded.ended_at is not None


class TestTriageTurnModel:
    """Test SQLAlchemy model for triage_turns."""

    def test_create_turn_record(self, db_session):
        """Turn record can be created with all fields."""
        # Create parent session first
        session = TriageSessionModel(session_id="turn-test-session")
        db_session.add(session)
        db_session.commit()

        turn = TriageTurnModel(
            session_id="turn-test-session",
            turn_index=1,
            user_text="I have chest pain",
            system_text="Can you describe the pain?",
            extracted_entities={"chief_complaint": "chest pain"},
            red_flags_triggered=["rf_cardiac_arrest_signs"],
            rules_triggered=["pre_check:critical_flag"],
            protocol_hits=[{"id": "PROTO-001", "title": "Chest Pain"}],
            protocol_citations=["PROTO-001"],
            confidence_score=0.85,
            confidence_breakdown={"raw_score": 1.0, "deductions": []},
            disposition="ER_NOW",
            escalation_required=True,
            safety_events=[{"type": "red_flag", "detail": "cardiac"}],
        )
        db_session.add(turn)
        db_session.commit()

        loaded = (
            db_session.query(TriageTurnModel)
            .filter_by(session_id="turn-test-session", turn_index=1)
            .one()
        )
        assert loaded.user_text == "I have chest pain"
        assert loaded.confidence_score == 0.85
        assert loaded.escalation_required is True
        assert loaded.red_flags_triggered == ["rf_cardiac_arrest_signs"]

    def test_unique_session_turn_constraint(self, db_session):
        """Cannot create two turns with same (session_id, turn_index)."""
        session = TriageSessionModel(session_id="unique-test")
        db_session.add(session)
        db_session.commit()

        turn1 = TriageTurnModel(
            session_id="unique-test", turn_index=1, disposition="UNDECIDED"
        )
        db_session.add(turn1)
        db_session.commit()

        turn2 = TriageTurnModel(
            session_id="unique-test", turn_index=1, disposition="ROUTINE"
        )
        db_session.add(turn2)
        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()

    def test_cascade_delete(self, db_session):
        """Deleting session cascades to turns."""
        session = TriageSessionModel(session_id="cascade-test")
        db_session.add(session)
        db_session.commit()

        turn = TriageTurnModel(session_id="cascade-test", turn_index=1)
        db_session.add(turn)
        db_session.commit()

        db_session.delete(session)
        db_session.commit()

        remaining = (
            db_session.query(TriageTurnModel).filter_by(session_id="cascade-test").all()
        )
        assert len(remaining) == 0


# ---------------------------------------------------------------------------
# PostgresStorage CRUD Tests
# ---------------------------------------------------------------------------


class TestPostgresStorageCRUD:
    """Test PostgresStorage interface using SQLite backend."""

    def test_create_session(self, postgres_storage):
        """create_session creates both in-memory and DB records."""
        session = postgres_storage.create_session(call_sid="CALL-001")
        assert session.session_id
        assert session.call_sid == "CALL-001"

        # Check DB record
        db_record = postgres_storage.get_session_record(session.session_id)
        assert db_record is not None
        assert db_record.channel == "twilio"
        assert db_record.status == "active"

    def test_create_session_api_channel(self, postgres_storage):
        """create_session without call_sid uses api channel."""
        session = postgres_storage.create_session()
        db_record = postgres_storage.get_session_record(session.session_id)
        assert db_record.channel == "api"

    def test_load_session(self, postgres_storage):
        """load_session returns in-memory session."""
        session = postgres_storage.create_session()
        loaded = postgres_storage.load_session(session.session_id)
        assert loaded is not None
        assert loaded.session_id == session.session_id

    def test_load_session_by_call(self, postgres_storage):
        """load_session_by_call resolves call_sid to session."""
        session = postgres_storage.create_session(call_sid="CALL-002")
        loaded = postgres_storage.load_session_by_call("CALL-002")
        assert loaded is not None
        assert loaded.session_id == session.session_id

    def test_load_session_by_call_tolerates_non_active_db_status(
        self, postgres_storage
    ):
        """Twilio gathers should recover from DB status/session-state drift."""
        session = postgres_storage.create_session(call_sid="CALL-STATUS-DRIFT")
        with postgres_storage._SessionFactory() as db:
            record = db.get(TriageSessionModel, session.session_id)
            record.status = "ended"
            db.commit()

        loaded = postgres_storage.load_session_by_call("CALL-STATUS-DRIFT")
        assert loaded is not None
        assert loaded.session_id == session.session_id
        assert loaded.is_finalized is False

    def test_load_session_by_call_recovers_from_caller_id_drift(self, postgres_storage):
        """Serialized call_sid fallback keeps Twilio calls alive if caller_id drifts."""
        session = postgres_storage.create_session(call_sid="CALL-ID-DRIFT")
        with postgres_storage._SessionFactory() as db:
            record = db.get(TriageSessionModel, session.session_id)
            record.caller_id = None
            db.commit()

        loaded = postgres_storage.load_session_by_call("CALL-ID-DRIFT")
        assert loaded is not None
        assert loaded.session_id == session.session_id
        assert loaded.call_sid == "CALL-ID-DRIFT"

    def test_save_session_restores_caller_id(self, postgres_storage):
        """Persisting a Twilio session should keep caller_id queryable."""
        session = postgres_storage.create_session(call_sid="CALL-RESTORE-ID")
        with postgres_storage._SessionFactory() as db:
            record = db.get(TriageSessionModel, session.session_id)
            record.caller_id = None
            db.commit()

        postgres_storage.save_session(session)

        with postgres_storage._SessionFactory() as db:
            record = db.get(TriageSessionModel, session.session_id)
            assert record.caller_id == "CALL-RESTORE-ID"

    def test_load_nonexistent(self, postgres_storage):
        """load_session returns None for unknown ID."""
        assert postgres_storage.load_session("nonexistent") is None

    def test_save_session_persists_trace(self, postgres_storage):
        """save_session persists decision trace entries as turns."""
        session = postgres_storage.create_session()

        # Add a decision trace entry
        entry = DecisionTraceEntry(
            turn_number=1,
            user_text="I have a headache",
            extracted_entities={"chief_complaint": "headache"},
            red_flags_triggered=[],
            rules_triggered=[],
            confidence_score=0.85,
            disposition="UNDECIDED",
            escalation_required=False,
            system_response="How long have you had the headache?",
        )
        session.decision_trace.append(entry)

        postgres_storage.save_session(session)

        turns = postgres_storage.get_turn_records(session.session_id)
        assert len(turns) == 1
        assert turns[0].turn_index == 1
        assert turns[0].confidence_score == 0.85

    def test_delete_session(self, postgres_storage):
        """delete_session removes both in-memory and DB records."""
        session = postgres_storage.create_session(call_sid="CALL-DEL")
        sid = session.session_id

        postgres_storage.delete_session(sid)

        assert postgres_storage.load_session(sid) is None
        assert postgres_storage.load_session_by_call("CALL-DEL") is None
        assert postgres_storage.get_session_record(sid) is None

    def test_save_finalized_session(self, postgres_storage):
        """Finalized session updates status and ended_at."""
        session = postgres_storage.create_session()
        session.is_finalized = True

        postgres_storage.save_session(session)

        db_record = postgres_storage.get_session_record(session.session_id)
        assert db_record.status == "ended"
        assert db_record.ended_at is not None


# ---------------------------------------------------------------------------
# PHI Control Tests
# ---------------------------------------------------------------------------


class TestPHIControl:
    """Test that STORE_PHI setting controls text field storage."""

    def test_phi_disabled_no_text_stored(self, postgres_storage):
        """When STORE_PHI=false, user_text and system_text are not stored."""
        session = postgres_storage.create_session()

        entry = DecisionTraceEntry(
            turn_number=1,
            user_text="My name is John and I have chest pain",
            extracted_entities={},
            red_flags_triggered=[],
            rules_triggered=[],
            confidence_score=0.5,
            disposition="UNDECIDED",
            escalation_required=False,
            system_response="Tell me more about the pain",
        )
        session.decision_trace.append(entry)

        # Patch STORE_PHI to False
        with patch("src.storage.postgres.STORE_PHI", False):
            postgres_storage.save_session(session)

        turns = postgres_storage.get_turn_records(session.session_id)
        assert len(turns) == 1
        # With converged PHI masking, text is stored redacted (not null)
        assert turns[0].user_text is not None
        assert "John" not in turns[0].user_text
        assert "[REDACTED" in turns[0].user_text
        assert turns[0].system_text is not None

    def test_phi_enabled_text_stored(self, postgres_storage):
        """When STORE_PHI=true, user_text and system_text are stored."""
        session = postgres_storage.create_session()

        entry = DecisionTraceEntry(
            turn_number=1,
            user_text="My name is John and I have chest pain",
            extracted_entities={},
            red_flags_triggered=[],
            rules_triggered=[],
            confidence_score=0.5,
            disposition="UNDECIDED",
            escalation_required=False,
            system_response="Tell me more about the pain",
        )
        session.decision_trace.append(entry)

        with patch("src.storage.postgres.STORE_PHI", True):
            postgres_storage.save_session(session)

        turns = postgres_storage.get_turn_records(session.session_id)
        assert len(turns) == 1
        assert turns[0].user_text == "My name is John and I have chest pain"
        assert turns[0].system_text == "Tell me more about the pain"


# ---------------------------------------------------------------------------
# Migration Test
# ---------------------------------------------------------------------------


class TestMigration:
    """Test that SQLAlchemy models create tables correctly."""

    def test_tables_created(self, sqlite_engine):
        """All expected tables exist after create_all."""
        from sqlalchemy import inspect

        inspector = inspect(sqlite_engine)
        table_names = inspector.get_table_names()
        assert "triage_sessions" in table_names
        assert "triage_turns" in table_names

    def test_session_columns(self, sqlite_engine):
        """triage_sessions has all expected columns."""
        from sqlalchemy import inspect

        inspector = inspect(sqlite_engine)
        columns = {c["name"] for c in inspector.get_columns("triage_sessions")}
        expected = {
            "session_id",
            "channel",
            "created_at",
            "updated_at",
            "ended_at",
            "status",
            "final_disposition",
            "escalation_reason",
            "model_name",
            "model_version",
            "protocol_version_used",
            "metadata",
        }
        assert expected.issubset(columns)

    def test_turn_columns(self, sqlite_engine):
        """triage_turns has all expected columns."""
        from sqlalchemy import inspect

        inspector = inspect(sqlite_engine)
        columns = {c["name"] for c in inspector.get_columns("triage_turns")}
        expected = {
            "id",
            "session_id",
            "turn_index",
            "timestamp",
            "user_text",
            "system_text",
            "extracted_entities",
            "red_flags_triggered",
            "rules_triggered",
            "protocol_hits",
            "protocol_citations",
            "confidence_score",
            "confidence_breakdown",
            "disposition",
            "next_action",
            "escalation_required",
            "safety_events",
        }
        assert expected.issubset(columns)


# ---------------------------------------------------------------------------
# Storage Factory Tests
# ---------------------------------------------------------------------------


class TestStorageFactory:
    """Test storage backend factory selection."""

    def test_memory_backend_default(self):
        """Default STORAGE_BACKEND=memory returns InMemoryOrchestratorStorage."""
        from src.storage.factory import reset_storage_backend

        reset_storage_backend()

        # Patch src.config directly — factory now reads live config at call time
        with (
            patch("src.config.STORAGE_BACKEND", "memory"),
            patch("src.config.ENVIRONMENT", "development"),
        ):
            from src.storage.factory import get_storage_backend
            from src.storage.factory import reset_storage_backend as reset

            reset()  # Clear singleton
            backend = get_storage_backend()
            from src.storage.memory import InMemoryOrchestratorStorage

            assert isinstance(backend, InMemoryOrchestratorStorage)
            reset()

    def test_postgres_backend_requires_url(self):
        """STORAGE_BACKEND=postgres without DATABASE_URL raises."""
        from src.storage.factory import reset_storage_backend

        reset_storage_backend()

        # Patch src.config directly — factory now reads live config at call time
        with (
            patch("src.config.STORAGE_BACKEND", "postgres"),
            patch("src.config.DATABASE_URL", None),
        ):
            from src.storage.factory import get_storage_backend
            from src.storage.factory import reset_storage_backend as reset

            reset()
            with pytest.raises(RuntimeError, match="DATABASE_URL"):
                get_storage_backend()
            reset()
