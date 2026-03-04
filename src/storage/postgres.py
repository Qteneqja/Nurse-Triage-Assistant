"""
PostgreSQL Storage Backend — Phase 3

Implements StorageInterface using SQLAlchemy 2.0 async-compatible sessions.
Uses synchronous engine for simplicity (runs in thread pool with FastAPI).

PHI control: respects STORE_PHI setting for text fields.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session as SASession, sessionmaker

from src.config import DATABASE_URL, ENVIRONMENT, STORE_PHI
from src.orchestrator.schemas import AuditTrace, OrchestratorSession
from src.storage.interface import StorageInterface
from src.storage.models import Base, TriageSessionModel, TriageTurnModel
from src.safety.phi_masking import mask_phi

logger = logging.getLogger(__name__)


class PostgresStorage(StorageInterface):
    """Postgres-backed storage for orchestrator sessions.

    Stateless: no in-memory cache. Session state is serialized to the
    metadata_json column and deserialized on every load. Crash-safe.
    """

    def __init__(self, database_url: str | None = None) -> None:
        url = database_url or DATABASE_URL
        if not url:
            raise RuntimeError("DATABASE_URL is required for PostgresStorage")

        self._engine = create_engine(url, pool_pre_ping=True, echo=False)
        self._SessionFactory = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )

    def initialize(self) -> None:
        """Create tables if they don't exist.

        In production, tables MUST be created via Alembic migrations.
        create_all is only used in development/test environments.
        """
        if ENVIRONMENT == "production":
            logger.info(
                "[PostgresStorage] Production mode — skipping create_all. "
                "Run 'alembic upgrade head' to apply migrations."
            )
            return
        Base.metadata.create_all(self._engine)
        logger.info("[PostgresStorage] Tables initialized (dev mode)")

    def check_connectivity(self) -> bool:
        """Check database connectivity. Returns True if DB is reachable."""
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            logger.error(f"[PostgresStorage] Connectivity check failed: {exc}")
            return False

    # ---- StorageInterface implementation ----

    def create_session(self, call_sid: str | None = None) -> OrchestratorSession:
        session_id = str(uuid.uuid4())
        session = OrchestratorSession(
            session_id=session_id,
            call_sid=call_sid,
            audit_trace=AuditTrace(session_id=session_id, call_sid=call_sid),
        )

        # Persist to DB with full serialized state
        with self._SessionFactory() as db:
            db_session = TriageSessionModel(
                session_id=session_id,
                caller_id=call_sid,
                channel="twilio" if call_sid else "api",
                status="active",
                metadata_json={"session_state": session.model_dump(mode="json")},
            )
            db.add(db_session)
            db.commit()

        logger.info(f"[PostgresStorage] Created session {session_id} (call={call_sid})")
        return session

    def save_session(self, session: OrchestratorSession) -> None:
        with self._SessionFactory() as db:
            db_session = db.get(TriageSessionModel, session.session_id)
            if db_session is None:
                db_session = TriageSessionModel(session_id=session.session_id)
                db.add(db_session)

            db_session.updated_at = datetime.now(timezone.utc)
            db_session.status = self._compute_status(session)
            db_session.metadata_json = {
                "session_state": session.model_dump(mode="json")
            }

            if session.is_finalized:
                db_session.ended_at = datetime.now(timezone.utc)
                if session.finalize_output:
                    db_session.final_disposition = (
                        session.finalize_output.disposition.value
                    )

            # Persist decision trace as turns
            self._sync_turns(db, session)
            db.commit()

    def load_session(self, session_id: str) -> Optional[OrchestratorSession]:
        with self._SessionFactory() as db:
            db_session = db.get(TriageSessionModel, session_id)
            if db_session is None or db_session.deleted_at is not None:
                return None
            return self._deserialize_session(db_session)

    def load_session_by_call(self, call_sid: str) -> Optional[OrchestratorSession]:
        with self._SessionFactory() as db:
            db_session = db.execute(
                select(TriageSessionModel).where(
                    TriageSessionModel.caller_id == call_sid,
                    TriageSessionModel.deleted_at.is_(None),
                    TriageSessionModel.status == "active",
                )
            ).scalar_one_or_none()
            if db_session is None:
                return None
            return self._deserialize_session(db_session)

    def _deserialize_session(
        self, db_session: TriageSessionModel
    ) -> Optional[OrchestratorSession]:
        """Deserialize OrchestratorSession from metadata_json column."""
        meta = db_session.metadata_json or {}
        state_data = meta.get("session_state")
        if state_data is None:
            logger.warning(
                f"[PostgresStorage] No session_state in metadata for {db_session.session_id}"
            )
            return None
        try:
            return OrchestratorSession.model_validate(state_data)
        except Exception as exc:
            logger.error(
                f"[PostgresStorage] Failed to deserialize session {db_session.session_id}: {exc}"
            )
            return None

    def delete_session(self, session_id: str) -> None:
        with self._SessionFactory() as db:
            db_session = db.get(TriageSessionModel, session_id)
            if db_session:
                db_session.deleted_at = datetime.now(timezone.utc)
                db.commit()

    # ---- Persistence helpers ----

    def _sync_turns(self, db: SASession, session: OrchestratorSession) -> None:
        """Sync decision trace entries to triage_turns table."""
        existing_count = (
            db.execute(
                select(TriageTurnModel.turn_index).where(
                    TriageTurnModel.session_id == session.session_id
                )
            )
            .scalars()
            .all()
        )
        existing_indices = set(existing_count)

        for entry in session.decision_trace:
            if entry.turn_number in existing_indices:
                continue  # Already persisted

            turn = TriageTurnModel(
                session_id=session.session_id,
                turn_index=entry.turn_number,
                timestamp=entry.timestamp,
                user_text=entry.user_text
                if STORE_PHI
                else (mask_phi(entry.user_text) if entry.user_text else None),
                system_text=entry.system_response
                if STORE_PHI
                else (
                    mask_phi(entry.system_response) if entry.system_response else None
                ),
                phi_masked=not STORE_PHI,
                extracted_entities=entry.extracted_entities
                if entry.extracted_entities
                else None,
                red_flags_triggered=entry.red_flags_triggered
                if entry.red_flags_triggered
                else None,
                rules_triggered=entry.rules_triggered
                if entry.rules_triggered
                else None,
                protocol_hits=[h.model_dump() for h in entry.protocol_hits]
                if entry.protocol_hits
                else None,
                protocol_citations=entry.protocol_citations
                if entry.protocol_citations
                else None,
                confidence_score=entry.confidence_score,
                confidence_breakdown=entry.confidence_breakdown.model_dump()
                if entry.confidence_breakdown
                else None,
                disposition=entry.disposition,
                escalation_required=entry.escalation_required,
                safety_events=None,  # Populated if safety flags exist
            )
            db.add(turn)

    @staticmethod
    def _compute_status(session: OrchestratorSession) -> str:
        if session.is_finalized:
            # Check if any escalation happened
            for entry in session.decision_trace:
                if entry.escalation_required:
                    return "escalated"
            return "ended"
        return "active"

    # ---- Query helpers (for observability / reporting) ----

    def get_session_record(self, session_id: str) -> Optional[TriageSessionModel]:
        """Load the raw DB record for a session (excludes soft-deleted)."""
        with self._SessionFactory() as db:
            record = db.get(TriageSessionModel, session_id)
            if record is None or record.deleted_at is not None:
                return None
            return record

    def get_turn_records(self, session_id: str) -> list[TriageTurnModel]:
        """Load all turn records for a session."""
        with self._SessionFactory() as db:
            result = db.execute(
                select(TriageTurnModel)
                .where(TriageTurnModel.session_id == session_id)
                .order_by(TriageTurnModel.turn_index)
            )
            return list(result.scalars().all())

    def get_active_session_count(self) -> int:
        """Return the number of active sessions from DB."""
        from sqlalchemy import func

        with self._SessionFactory() as db:
            count = db.execute(
                select(func.count())
                .select_from(TriageSessionModel)
                .where(
                    TriageSessionModel.status == "active",
                    TriageSessionModel.deleted_at.is_(None),
                )
            ).scalar()
            return count or 0
