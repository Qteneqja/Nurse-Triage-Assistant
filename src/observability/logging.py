"""
Structured JSON Logging — Phase 5

Provides structured JSON log formatter and per-request context injection.
Every log line includes request_id, call_sid, session_id, turn_index, etc.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

# Context variables for per-request structured fields
_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_call_sid: ContextVar[Optional[str]] = ContextVar("call_sid", default=None)
_session_id: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
_turn_index: ContextVar[Optional[int]] = ContextVar("turn_index", default=None)
_escalation_required: ContextVar[Optional[bool]] = ContextVar("escalation_required", default=None)
_disposition: ContextVar[Optional[str]] = ContextVar("disposition", default=None)


def set_log_context(
    session_id: Optional[str] = None,
    turn_index: Optional[int] = None,
    escalation_required: Optional[bool] = None,
    disposition: Optional[str] = None,
    request_id: Optional[str] = None,
    call_sid: Optional[str] = None,
) -> None:
    """Set structured log context for the current request/task."""
    if request_id is not None:
        _request_id.set(request_id)
    if call_sid is not None:
        _call_sid.set(call_sid)
    if session_id is not None:
        _session_id.set(session_id)
    if turn_index is not None:
        _turn_index.set(turn_index)
    if escalation_required is not None:
        _escalation_required.set(escalation_required)
    if disposition is not None:
        _disposition.set(disposition)


def clear_log_context() -> None:
    """Clear all structured log context vars."""
    _request_id.set(None)
    _call_sid.set(None)
    _session_id.set(None)
    _turn_index.set(None)
    _escalation_required.set(None)
    _disposition.set(None)


class StructuredJSONFormatter(logging.Formatter):
    """JSON log formatter that includes structured context fields."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _request_id.get(None),
            "call_sid": _call_sid.get(None),
            "session_id": _session_id.get(None),
            "turn_index": _turn_index.get(None),
            "escalation_required": _escalation_required.get(None),
            "disposition": _disposition.get(None),
        }

        # Add extra fields if present
        for key in ("llm_latency_ms", "total_turn_latency_ms"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        # Add exception info if present
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Remove None values for cleaner output
        log_entry = {k: v for k, v in log_entry.items() if v is not None}

        return json.dumps(log_entry, default=str)


def configure_structured_logging(log_format: str = "json", level: int | str = logging.INFO) -> None:
    """Configure the root logger with structured JSON or text format.

    Args:
        log_format: "json" for JSON lines, "text" for human-readable.
        level: Logging level (int like logging.INFO, or string like "WARNING").
    """
    resolved_level = level if isinstance(level, int) else getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()

    # Remove existing handlers to avoid duplicates
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(resolved_level)

    if log_format == "json":
        handler.setFormatter(StructuredJSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        )

    root.addHandler(handler)
    root.setLevel(resolved_level)
