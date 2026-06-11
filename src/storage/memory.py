"""
In-Memory Storage for Orchestrator Sessions

Implements StorageInterface with a simple dict backend + TTL cleanup.
Structured for easy swap to Redis or another persistent store later.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, Optional

from src.orchestrator.schemas import OrchestratorSession, AuditTrace
from src.storage.interface import StorageInterface

logger = logging.getLogger(__name__)

SESSION_TTL_MINUTES = 60
CLEANUP_INTERVAL_MINUTES = 5


class InMemoryOrchestratorStorage(StorageInterface):
    """In-memory orchestrator session storage with TTL-based cleanup."""

    def __init__(self) -> None:
        self._sessions: Dict[str, OrchestratorSession] = {}
        self._call_index: Dict[str, str] = {}  # CallSid -> session_id
        self._expiry: Dict[str, datetime] = {}  # session_id -> expiry time
        self._extractions: Dict[str, list[Any]] = {}
        self._record_status_events: Dict[str, list[dict[str, Any]]] = {}
        self._cleanup_task: asyncio.Task | None = None

    def start_cleanup(self) -> None:
        """Start the background cleanup loop (call from async context)."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        """Periodically remove expired sessions."""
        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL_MINUTES * 60)
                now = datetime.now(UTC)
                expired = [sid for sid, exp in self._expiry.items() if exp < now]
                for sid in expired:
                    self._remove(sid)
                if expired:
                    logger.info(
                        f"Cleaned up {len(expired)} expired orchestrator sessions"
                    )
            except Exception as e:
                logger.error(f"Orchestrator storage cleanup error: {e}")

    # ---- StorageInterface implementation ----

    def create_session(
        self,
        call_sid: str | None = None,
        workflow_route: Any | None = None,
    ) -> OrchestratorSession:
        """Create and persist a new orchestrator session."""
        session_id = str(uuid.uuid4())
        session = OrchestratorSession(
            session_id=session_id,
            call_sid=call_sid,
            audit_trace=AuditTrace(session_id=session_id, call_sid=call_sid),
        )
        _apply_workflow_route(session, workflow_route)
        self._sessions[session_id] = session
        self._expiry[session_id] = datetime.now(UTC) + timedelta(
            minutes=SESSION_TTL_MINUTES
        )
        if call_sid:
            self._call_index[call_sid] = session_id
        logger.info(f"Created orchestrator session {session_id} (call={call_sid})")
        return session

    def save_session(self, session: OrchestratorSession) -> None:
        """Persist session (in-memory = just update dict)."""
        self._sessions[session.session_id] = session
        if session.call_sid:
            self._call_index[session.call_sid] = session.session_id
        # refresh TTL
        self._expiry[session.session_id] = datetime.now(UTC) + timedelta(
            minutes=SESSION_TTL_MINUTES
        )

    def load_session(self, session_id: str) -> Optional[OrchestratorSession]:
        """Load session by ID, returning None if expired."""
        if session_id in self._expiry and self._expiry[session_id] < datetime.now(UTC):
            self._remove(session_id)
            return None
        return self._sessions.get(session_id)

    def load_session_by_call(self, call_sid: str) -> Optional[OrchestratorSession]:
        """Load session by Twilio CallSid."""
        session_id = self._call_index.get(call_sid)
        if session_id is None:
            return None
        return self.load_session(session_id)

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        self._remove(session_id)

    def save_extraction(self, extraction: Any) -> None:
        """Persist a post-call extraction in memory for tests/dev."""
        session_id = getattr(extraction, "session_id", None)
        if not session_id:
            return
        self._extractions.setdefault(session_id, []).append(extraction)

    def get_extractions(self, session_id: str) -> list[Any]:
        """Return saved extraction results for a session."""
        return list(self._extractions.get(session_id, []))

    def list_recent_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        vertical_key: str | None = None,
        workflow_id: str | None = None,
        status: str | None = None,
    ) -> list[OrchestratorSession]:
        """Return recent sessions for dashboard/admin read models."""
        now = datetime.now(UTC)
        sessions = [
            session
            for session_id, session in self._sessions.items()
            if self._expiry.get(session_id, now) >= now
        ]

        if vertical_key:
            sessions = [s for s in sessions if s.vertical_key == vertical_key]
        if workflow_id:
            sessions = [s for s in sessions if s.workflow_id == workflow_id]
        if status:
            sessions = [
                s
                for s in sessions
                if ("ended" if s.is_finalized else "active") == status
            ]

        sessions.sort(key=lambda s: s.created_at, reverse=True)
        return sessions[offset : offset + limit]

    def get_session_turns(self, session_id: str) -> list[Any]:
        """Return a best-effort turn history for dashboard/admin views."""
        session = self.load_session(session_id)
        if session is None:
            return []
        if session.decision_trace:
            return list(session.decision_trace)
        return list(session.conversation)

    def get_session_extractions(self, session_id: str) -> list[Any]:
        """Return saved extraction results for a session."""
        return self.get_extractions(session_id)

    def append_record_status_event(
        self,
        session_id: str,
        status: str,
        actor: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": str(uuid.uuid4()),
            "session_id": session_id,
            "status": status,
            "actor": actor,
            "note": note,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._record_status_events.setdefault(session_id, []).append(event)
        return event

    def get_record_status_events(self, session_id: str) -> list[dict[str, Any]]:
        events = list(self._record_status_events.get(session_id, []))
        events.sort(key=lambda e: e["created_at"], reverse=True)
        return events

    def list_organizations(self) -> list[Any]:
        """Return organization-like rows inferred from in-memory sessions."""
        rows: dict[str, dict[str, Any]] = {}
        for session in self.list_recent_sessions(limit=1000):
            if not session.organization_id:
                continue
            row = rows.setdefault(
                session.organization_id,
                {
                    "organization_id": session.organization_id,
                    "name": session.organization_id,
                    "slug": None,
                    "status": "active",
                    "verticals": set(),
                    "workflows": set(),
                },
            )
            if session.vertical_key:
                row["verticals"].add(session.vertical_key)
            if session.workflow_id:
                row["workflows"].add(session.workflow_id)
        return [
            {
                **row,
                "verticals": sorted(row["verticals"]),
                "workflows": sorted(row["workflows"]),
            }
            for row in rows.values()
        ]

    # ---- Internals ----

    def _remove(self, session_id: str) -> None:
        """Remove session and all index entries."""
        session = self._sessions.pop(session_id, None)
        self._expiry.pop(session_id, None)
        self._extractions.pop(session_id, None)
        if session and session.call_sid:
            self._call_index.pop(session.call_sid, None)

    def get_active_session_count(self) -> int:
        """Return the number of currently tracked sessions."""
        return len(self._sessions)


def _apply_workflow_route(
    session: OrchestratorSession,
    workflow_route: Any | None,
) -> None:
    if workflow_route is None:
        return
    session.organization_id = getattr(workflow_route, "organization_id", None)
    session.vertical_key = getattr(workflow_route, "vertical_key", None)
    session.workflow_id = getattr(workflow_route, "workflow_id", None)
    session.workflow_version = getattr(workflow_route, "workflow_version", None)
    session.phone_number_id = getattr(workflow_route, "phone_number_id", None)
    session.channel_metadata["route"] = {
        "organization_id": session.organization_id,
        "vertical_key": session.vertical_key,
        "workflow_id": session.workflow_id,
        "workflow_version": session.workflow_version,
        "phone_number_id": session.phone_number_id,
        "fallback_used": getattr(workflow_route, "fallback_used", False),
        "fallback_reason": getattr(workflow_route, "fallback_reason", None),
        "audit_metadata": getattr(workflow_route, "audit_metadata", {}),
    }


# ---- Access through factory ----
# All session access MUST go through src.storage.factory.get_storage_backend()
# or through src.storage.session_repository.get_session_repository().
# No direct singleton here — the factory selects InMemoryOrchestratorStorage
# when STORAGE_BACKEND=memory.
