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
from typing import Dict, Optional

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

    def create_session(self, call_sid: str | None = None) -> OrchestratorSession:
        """Create and persist a new orchestrator session."""
        session_id = str(uuid.uuid4())
        session = OrchestratorSession(
            session_id=session_id,
            call_sid=call_sid,
            audit_trace=AuditTrace(session_id=session_id, call_sid=call_sid),
        )
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

    # ---- Internals ----

    def _remove(self, session_id: str) -> None:
        """Remove session and all index entries."""
        session = self._sessions.pop(session_id, None)
        self._expiry.pop(session_id, None)
        if session and session.call_sid:
            self._call_index.pop(session.call_sid, None)

    def get_active_session_count(self) -> int:
        """Return the number of currently tracked sessions."""
        return len(self._sessions)


# ---- Access through factory ----
# All session access MUST go through src.storage.factory.get_storage_backend()
# or through src.storage.session_repository.get_session_repository().
# No direct singleton here — the factory selects InMemoryOrchestratorStorage
# when STORAGE_BACKEND=memory.
