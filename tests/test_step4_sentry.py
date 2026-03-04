"""
Tests for Sentry Error Monitoring Integration
Purpose: Verify PHI scrubbing, conditional init, and safe data handling.
Date Created: 2026-03-02
Step: Production Hardening — Step 4

Tests:
  1. No init without DSN
  2. Init with DSN
  3. Scrub request body
  4. Scrub headers
  5. Preserve structure without request data
  6. Scrub breadcrumbs
"""

from __future__ import annotations

import os
from copy import deepcopy
from unittest.mock import patch, MagicMock


from src.observability.sentry_integration import (
    _scrub_phi,
    init_sentry,
    set_sentry_context,
    capture_llm_failure,
    capture_json_validation_failure,
    capture_db_failure,
    add_safety_gate_breadcrumb,
)


# ---------------------------------------------------------------------------
# Test 1: No init without DSN
# ---------------------------------------------------------------------------


def test_sentry_not_initialized_without_dsn():
    """Sentry must NOT initialize when SENTRY_DSN is empty or missing."""
    with patch.dict(os.environ, {"SENTRY_DSN": ""}, clear=False):
        result = init_sentry()
        assert result is False


def test_sentry_not_initialized_without_dsn_unset():
    """Sentry must NOT initialize when SENTRY_DSN is not in environment at all."""
    env = os.environ.copy()
    env.pop("SENTRY_DSN", None)
    with patch.dict(os.environ, env, clear=True):
        result = init_sentry()
        assert result is False


# ---------------------------------------------------------------------------
# Test 2: Init with DSN
# ---------------------------------------------------------------------------


def test_sentry_initialized_with_dsn():
    """Sentry should initialize when SENTRY_DSN is set to a valid value."""
    mock_init = MagicMock()
    with patch.dict(os.environ, {"SENTRY_DSN": "https://key@sentry.io/123"}):
        with patch(
            "src.observability.sentry_integration.sentry_sdk", create=True
        ) as mock_sdk:
            # Need to patch the import within the function
            with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
                mock_sdk.init = mock_init
                result = init_sentry()
                assert result is True
                mock_init.assert_called_once()
                # Verify send_default_pii=False (HIPAA critical)
                call_kwargs = mock_init.call_args
                assert (
                    call_kwargs[1].get("send_default_pii") is False
                    or call_kwargs.kwargs.get("send_default_pii") is False
                )


# ---------------------------------------------------------------------------
# Test 3: Scrub request body
# ---------------------------------------------------------------------------


def test_scrub_phi_removes_request_body():
    """PHI scrubbing must remove request body data — may contain transcript."""
    event = {
        "request": {
            "url": "https://example.com/api/triage",
            "method": "POST",
            "data": {"transcript": "Patient reports chest pain", "age": 55},
            "cookies": "session=abc123",
            "headers": {
                "content-type": "application/json",
                "authorization": "Bearer secret-token",
                "user-agent": "NurseTriage/1.0",
            },
        },
        "exception": {"values": [{"type": "LLMCallError"}]},
    }

    result = _scrub_phi(event, {})

    assert result is not None
    assert result["request"]["data"] == "[REDACTED — PHI SAFEGUARD]"
    assert result["request"]["cookies"] == "[REDACTED]"
    # Exception info should be preserved
    assert result["exception"]["values"][0]["type"] == "LLMCallError"


# ---------------------------------------------------------------------------
# Test 4: Scrub headers
# ---------------------------------------------------------------------------


def test_scrub_phi_filters_headers():
    """Only allowlisted headers should survive PHI scrubbing."""
    event = {
        "request": {
            "url": "https://example.com/api/triage",
            "headers": {
                "content-type": "application/json",
                "authorization": "Bearer secret-token",
                "user-agent": "NurseTriage/1.0",
                "x-request-id": "req-123",
                "x-forwarded-for": "1.2.3.4",
                "cookie": "session=abc",
                "x-custom-patient-name": "John Doe",
            },
        },
    }

    result = _scrub_phi(event, {})
    assert result is not None

    headers = result["request"]["headers"]
    assert "content-type" in headers
    assert "user-agent" in headers
    assert "x-request-id" in headers
    assert "x-forwarded-for" in headers
    # Sensitive headers must be removed
    assert "authorization" not in headers
    assert "cookie" not in headers
    assert "x-custom-patient-name" not in headers


# ---------------------------------------------------------------------------
# Test 5: Preserve structure without request data
# ---------------------------------------------------------------------------


def test_scrub_phi_preserves_non_request_data():
    """Events without request data should pass through unchanged."""
    event = {
        "level": "error",
        "logger": "src.llm.client",
        "exception": {
            "values": [
                {
                    "type": "LLMCallError",
                    "value": "LLM call failed: timeout",
                    "stacktrace": {"frames": [{"filename": "client.py"}]},
                }
            ]
        },
        "tags": {"environment": "production"},
    }

    original = deepcopy(event)
    result = _scrub_phi(event, {})

    assert result is not None
    # All non-request fields should be identical
    assert result["level"] == original["level"]
    assert result["logger"] == original["logger"]
    assert result["exception"] == original["exception"]
    assert result["tags"] == original["tags"]


# ---------------------------------------------------------------------------
# Test 6: Scrub breadcrumbs
# ---------------------------------------------------------------------------


def test_scrub_phi_redacts_breadcrumb_phi():
    """Breadcrumbs with PHI-risk data keys must be redacted."""
    event = {
        "breadcrumbs": {
            "values": [
                {
                    "category": "http",
                    "message": "POST /api/triage",
                    "data": {
                        "url": "https://example.com/api/triage",
                        "status_code": 200,
                    },
                },
                {
                    "category": "safety_gate",
                    "message": "Rule override",
                    "data": {
                        "rule_name": "rf_cardiac_arrest_signs",
                        "transcript_excerpt": "patient says chest hurts",
                        "patient_name": "John Doe",
                        "symptom_list": "chest pain, sweating",
                        "message": "some internal message",
                    },
                },
            ]
        },
    }

    result = _scrub_phi(event, {})
    assert result is not None

    # First breadcrumb: no PHI keywords → should be unchanged
    crumb0 = result["breadcrumbs"]["values"][0]
    assert crumb0["data"]["url"] == "https://example.com/api/triage"
    assert crumb0["data"]["status_code"] == 200

    # Second breadcrumb: PHI-risk keywords should be redacted
    crumb1 = result["breadcrumbs"]["values"][1]
    assert crumb1["data"]["rule_name"] == "rf_cardiac_arrest_signs"  # safe
    assert crumb1["data"]["transcript_excerpt"] == "[REDACTED — PHI SAFEGUARD]"
    assert crumb1["data"]["patient_name"] == "[REDACTED — PHI SAFEGUARD]"
    assert crumb1["data"]["symptom_list"] == "[REDACTED — PHI SAFEGUARD]"
    assert crumb1["data"]["message"] == "[REDACTED — PHI SAFEGUARD]"


# ---------------------------------------------------------------------------
# Bonus: capture functions don't crash when sentry is not installed
# ---------------------------------------------------------------------------


def test_capture_functions_no_crash_without_sentry():
    """Capture functions should silently no-op if sentry_sdk is not importable."""
    # These should not raise even if sentry_sdk.capture_message fails
    with patch.dict("sys.modules", {"sentry_sdk": None}):
        # Functions catch ImportError internally
        capture_llm_failure(model_name="deepseek-chat", error_type="Timeout")
        capture_json_validation_failure(schema_name="IntakeTurn", error_message="bad")
        capture_db_failure(error_type="ConnectionRefused")
        set_sentry_context(session_id="sess-123")
        add_safety_gate_breadcrumb(rule_name="rf_test", disposition_override="ER_NOW")
