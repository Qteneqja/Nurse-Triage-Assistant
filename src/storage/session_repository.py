"""
Session Repository — Stateless session persistence abstraction.

All session access goes through this module.
The underlying backend is selected by the storage factory.

Session state is ALWAYS loaded from the backend and persisted after
every mutation. No in-memory dictionaries outside the storage backend.
Crash/restart does not lose session state (when backed by Postgres).
"""

from __future__ import annotations

import logging
from typing import Optional

from src.orchestrator.schemas import OrchestratorSession
from src.storage.factory import get_storage_backend

logger = logging.getLogger(__name__)


class SessionRepository:
    """Canonical session persistence — delegates to the storage factory."""

    def __init__(self) -> None:
        self._backend = get_storage_backend()

    def create_session(self, call_sid: str | None = None) -> OrchestratorSession:
        """Create a new session and persist it."""
        session = self._backend.create_session(call_sid=call_sid)
        logger.info(f"[SessionRepo] Created session {session.session_id}")
        return session

    def load_session(self, session_id: str) -> Optional[OrchestratorSession]:
        """Load session by ID from the backend."""
        return self._backend.load_session(session_id)

    def load_session_by_call(self, call_sid: str) -> Optional[OrchestratorSession]:
        """Load session by Twilio CallSid."""
        return self._backend.load_session_by_call(call_sid)

    def persist_session(self, session: OrchestratorSession) -> None:
        """Persist session state after every turn."""
        self._backend.save_session(session)

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        self._backend.delete_session(session_id)

    def get_active_session_count(self) -> int:
        """Return active session count."""
        return self._backend.get_active_session_count()

    def check_connectivity(self) -> bool:
        """Check backend connectivity."""
        return self._backend.check_connectivity()


# Singleton
_session_repo: SessionRepository | None = None


def get_session_repository() -> SessionRepository:
    """Get or create the singleton SessionRepository."""
    global _session_repo
    if _session_repo is None:
        _session_repo = SessionRepository()
    return _session_repo


def reset_session_repository() -> None:
    """Reset the singleton. Used in testing."""
    global _session_repo
    _session_repo = None
