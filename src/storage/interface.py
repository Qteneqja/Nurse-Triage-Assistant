"""
Storage Interface

Abstract base class for session storage.
Allows swapping between in-memory (MVP) and Redis (future) implementations.
"""
from __future__ import annotations

import abc
from typing import Optional

from src.orchestrator.schemas import OrchestratorSession


class StorageInterface(abc.ABC):
    """Abstract interface for orchestrator session persistence."""

    @abc.abstractmethod
    def save_session(self, session: OrchestratorSession) -> None:
        """Persist an orchestrator session."""
        ...

    @abc.abstractmethod
    def load_session(self, session_id: str) -> Optional[OrchestratorSession]:
        """Load an orchestrator session by ID. Returns None if not found / expired."""
        ...

    @abc.abstractmethod
    def load_session_by_call(self, call_sid: str) -> Optional[OrchestratorSession]:
        """Load an orchestrator session by Twilio CallSid."""
        ...

    @abc.abstractmethod
    def delete_session(self, session_id: str) -> None:
        """Remove a session."""
        ...

    @abc.abstractmethod
    def create_session(self, call_sid: str | None = None) -> OrchestratorSession:
        """Create a new orchestrator session and persist it."""
        ...

    def check_connectivity(self) -> bool:
        """Check if the storage backend is reachable.

        Returns True by default.  Subclasses (e.g. PostgresStorage) may
        override to perform an actual connectivity check.
        """
        return True

    def get_active_session_count(self) -> int:
        """Return the number of currently active sessions.

        Subclasses should override for accurate counts.
        """
        return 0
