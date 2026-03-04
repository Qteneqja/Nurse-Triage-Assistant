"""
Sentry Error Monitoring Integration
Purpose: Capture exceptions and key failure events in production without leaking PHI.
Date Created: 2026-03-02
Step: Production Hardening — Step 4

PHI Safeguards (Defense in Depth):
  1. send_default_pii=False in SDK init
  2. before_send hook scrubs request bodies, cookies, headers, and breadcrumbs
  3. Explicit capture points never include content fields
  4. Three layers of protection before any data reaches Sentry
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Use a plain type alias so the module works even when sentry_sdk is not
# installed (all capture helpers already guard against ImportError).
try:
    from sentry_sdk.types import Event as _SentryEvent, Hint as _SentryHint
except Exception:  # ImportError or missing types stub
    _SentryEvent = Dict[str, Any]  # type: ignore[assignment,misc]
    _SentryHint = Dict[str, Any]  # type: ignore[assignment,misc]

# Header strings that are safe to forward to Sentry
_SAFE_HEADERS = {"content-type", "user-agent", "x-request-id", "x-forwarded-for"}

# Keywords in breadcrumb data keys that may contain PHI
_PHI_RISK_KEYWORDS = (
    "transcript",
    "symptom",
    "patient",
    "caller",
    "message",
    "body",
    "text",
    "content",
    "input",
)


def _scrub_phi(event: _SentryEvent, hint: _SentryHint) -> Optional[_SentryEvent]:  # type: ignore[assignment]
    """
    Safety net — strip any request body data that could contain PHI.
    We should never be sending PHI in the first place, but defense
    in depth is required for HIPAA compliance.
    """
    if "request" in event:
        if "data" in event["request"]:
            event["request"]["data"] = "[REDACTED — PHI SAFEGUARD]"
        if "cookies" in event["request"]:
            event["request"]["cookies"] = "[REDACTED]"
        if "headers" in event["request"]:
            safe_headers = {}
            for k, v in event["request"]["headers"].items():
                if k.lower() in _SAFE_HEADERS:
                    safe_headers[k] = v
            event["request"]["headers"] = safe_headers

    # Scrub breadcrumbs that might contain transcript data
    if "breadcrumbs" in event:
        for crumb in event.get("breadcrumbs", {}).get("values", []):
            if "data" in crumb and isinstance(crumb["data"], dict):
                for key in list(crumb["data"].keys()):
                    key_lower = key.lower()
                    if any(term in key_lower for term in _PHI_RISK_KEYWORDS):
                        crumb["data"][key] = "[REDACTED — PHI SAFEGUARD]"

    return event


def init_sentry() -> bool:
    """Initialize Sentry error monitoring.

    Only activates when SENTRY_DSN environment variable exists and is non-empty.
    Returns True if Sentry was initialized, False otherwise.
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("[Sentry] SENTRY_DSN not set — Sentry disabled (zero overhead)")
        return False

    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development")),
            traces_sample_rate=0.1,
            send_default_pii=False,  # CRITICAL — HIPAA requirement
            before_send=_scrub_phi,
        )
        logger.info("[Sentry] Initialized successfully (PHI scrubbing active)")
        return True
    except Exception as exc:
        logger.error(f"[Sentry] Failed to initialize: {exc}")
        return False


def set_sentry_context(
    session_id: Optional[str] = None,
    call_sid: Optional[str] = None,
    environment: Optional[str] = None,
) -> None:
    """Set Sentry tags for the current scope.

    NEVER attach: patient name, DOB, transcript, symptoms, address, phone, or any PHI.
    NEVER include transcript content in breadcrumbs, tags, or extra context.
    """
    try:
        import sentry_sdk

        if session_id:
            sentry_sdk.set_tag("session_id", session_id)
        if call_sid:
            sentry_sdk.set_tag("call_sid", call_sid)
        if environment:
            sentry_sdk.set_tag("environment", environment)
    except ImportError:
        pass
    except Exception:
        pass


def capture_llm_failure(
    model_name: str,
    timeout_duration: Optional[float] = None,
    retry_count: Optional[int] = None,
    error_type: Optional[str] = None,
) -> None:
    """Capture LLM timeout or API failure event.

    Captures: model name, timeout duration, retry count.
    Does NOT capture: prompt content, response content, transcript.
    """
    try:
        import sentry_sdk

        sentry_sdk.capture_message(
            f"LLM API failure: {error_type or 'unknown'}",
            level="error",
            extras={
                "model_name": model_name,
                "timeout_duration_s": timeout_duration,
                "retry_count": retry_count,
                "error_type": error_type,
                # NO prompt content, NO response content, NO transcript
            },
        )
    except ImportError:
        pass
    except Exception:
        pass


def capture_json_validation_failure(
    schema_name: str,
    error_message: str,
) -> None:
    """Capture JSON validation failure on LLM response.

    Captures: schema name, error message.
    Does NOT capture: raw LLM response (may contain PHI if transcript was in prompt).
    """
    try:
        import sentry_sdk

        sentry_sdk.capture_message(
            f"JSON validation failure: {schema_name}",
            level="warning",
            extras={
                "schema_name": schema_name,
                "validation_error": error_message,
                # NOT the raw LLM response — it may contain PHI
            },
        )
    except ImportError:
        pass
    except Exception:
        pass


def add_safety_gate_breadcrumb(
    rule_name: str,
    disposition_override: str,
) -> None:
    """Add a breadcrumb when deterministic rules override LLM.

    Captures: rule name, disposition override only.
    Does NOT include: transcript, symptoms, or any patient data.
    """
    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            category="safety_gate",
            message=f"Rule override: {rule_name} → {disposition_override}",
            level="info",
            data={
                "rule_name": rule_name,
                "disposition_override": disposition_override,
                # NO transcript, NO symptoms, NO patient data
            },
        )
    except ImportError:
        pass
    except Exception:
        pass


def capture_db_failure(error_type: str) -> None:
    """Capture database connection failure.

    Captures: error type name only.
    """
    try:
        import sentry_sdk

        sentry_sdk.capture_message(
            f"Database connection failure: {error_type}",
            level="error",
            extras={"error_type": error_type},
        )
    except ImportError:
        pass
    except Exception:
        pass
