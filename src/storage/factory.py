"""
Storage Backend Factory — Phase 4 Hardened

Selects and initializes the storage backend based on STORAGE_BACKEND env var.
Enforces Postgres as mandatory backend in production — no in-memory fallback.
"""
from __future__ import annotations

import logging
from typing import Optional

import src.config as _config
from src.storage.interface import StorageInterface
from src.observability.sentry_integration import capture_db_failure

logger = logging.getLogger(__name__)

# Module-level aliases so tests can patch src.storage.factory.STORAGE_BACKEND etc.
STORAGE_BACKEND: str = _config.STORAGE_BACKEND
ENVIRONMENT: str = _config.ENVIRONMENT
DATABASE_URL: Optional[str] = _config.DATABASE_URL

_storage_instance: Optional[StorageInterface] = None


def get_storage_backend() -> StorageInterface:
    """Get or create the singleton storage backend.

    Selection:
    - STORAGE_BACKEND=memory  → InMemoryOrchestratorStorage (dev/test ONLY)
    - STORAGE_BACKEND=postgres → PostgresStorage (required in production)

    Raises:
        RuntimeError: If production environment uses non-postgres backend.
    """
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    # Re-read from src.config at call time so that importlib.reload(src.config)
    # in tests (and env-var changes in production init code) are picked up
    # correctly.  Do NOT cache the values in module-level aliases — those are
    # frozen at import time and won't reflect reloads.
    import src.config as _live_cfg
    _env = _live_cfg.ENVIRONMENT
    _backend = _live_cfg.STORAGE_BACKEND
    _db_url = _live_cfg.DATABASE_URL

    # HARD ENFORCEMENT: Production requires Postgres
    if _env == "production" and _backend != "postgres":
        raise RuntimeError(
            "Production requires Postgres. "
            "Set STORAGE_BACKEND=postgres and provide DATABASE_URL."
        )

    if _backend == "postgres":
        from src.storage.postgres import PostgresStorage
        if not _db_url:
            capture_db_failure("missing_database_url")
            raise RuntimeError(
                "DATABASE_URL is required when STORAGE_BACKEND=postgres"
            )
        try:
            storage = PostgresStorage(_db_url)
            storage.initialize()
        except Exception as exc:
            capture_db_failure(type(exc).__name__)
            raise
        _storage_instance = storage
        logger.info("[StorageFactory] Using PostgresStorage backend")
    else:
        from src.storage.memory import InMemoryOrchestratorStorage
        storage = InMemoryOrchestratorStorage()
        _storage_instance = storage
        logger.info("[StorageFactory] Using InMemoryOrchestratorStorage backend (dev/test)")

    return _storage_instance


def reset_storage_backend() -> None:
    """Reset the singleton. Used in testing."""
    global _storage_instance
    _storage_instance = None
