"""
Storage Interface

Abstract base class for session storage.
Allows swapping between in-memory (MVP) and Redis (future) implementations.
"""

from __future__ import annotations

import abc
from typing import Any, Optional

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
    def create_session(
        self,
        call_sid: str | None = None,
        workflow_route: Any | None = None,
    ) -> OrchestratorSession:
        """Create a new orchestrator session and persist it."""
        ...

    def save_extraction(self, extraction: Any) -> None:
        """Persist a post-call extraction result when supported."""
        return None

    def list_recent_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        vertical_key: str | None = None,
        workflow_id: str | None = None,
        status: str | None = None,
    ) -> list[OrchestratorSession]:
        """Return recent sessions for read-only dashboards."""
        return []

    def get_session_turns(self, session_id: str) -> list[Any]:
        """Return stored turn records for read-only dashboards."""
        return []

    def get_session_extractions(self, session_id: str) -> list[Any]:
        """Return persisted extraction records for a session."""
        return []

    def append_record_status_event(
        self,
        session_id: str,
        status: str,
        actor: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Append an intake-record status change (PR 4 dashboard workflow).

        Every change is an immutable audit event (timestamp + actor); the
        record's current status is the latest event. Backends that do not
        support persistence raise NotImplementedError so callers fail loudly
        rather than silently dropping an audit trail.
        """
        raise NotImplementedError("record status events are not supported")

    def get_record_status_events(self, session_id: str) -> list[dict[str, Any]]:
        """Return the status audit trail for a session, newest first."""
        return []

    def save_enrichment_result(self, result: dict[str, Any]) -> None:
        """Persist a post-call enrichment output (shadow mode).

        Default is a logged no-op: enrichment is best-effort and must never
        fail a pipeline because a backend lacks support.
        """
        return None

    def get_enrichment_results(self, session_id: str) -> list[dict[str, Any]]:
        """Return enrichment outputs for a session, newest first."""
        return []

    def list_enrichment_results(
        self,
        limit: int = 200,
        feature: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent enrichment outputs across sessions (admin view)."""
        return []

    def list_organizations(self) -> list[Any]:
        """Return tenant organization records for read-only admin views."""
        return []

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
