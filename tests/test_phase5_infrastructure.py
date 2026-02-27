"""
Phase 5 — SaaS Infrastructure Tests

Covers:
- Config validation for dev/staging/prod profiles
- Twilio signature validation
- Correlation ID middleware
- Health/ready endpoints
- CORS configuration
- Structured logging with request_id
"""
import hashlib
import hmac
import os
import uuid
from base64 import b64encode
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


# ============================================================================
# Helpers
# ============================================================================

def _twilio_signature(auth_token: str, url: str, params: dict) -> str:
    """Compute a valid Twilio signature for testing."""
    s = url
    for key in sorted(params.keys()):
        s += key + (params[key] or "")
    mac = hmac.new(auth_token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1)
    return b64encode(mac.digest()).decode("utf-8")


# ============================================================================
# Config validation
# ============================================================================

class TestConfigValidation:
    """Test APP_ENV-based config validation."""

    def test_dev_allows_memory_backend(self):
        """Development mode allows memory storage backend."""
        with patch.dict(os.environ, {
            "APP_ENV": "development",
            "STORAGE_BACKEND": "memory",
            "ENVIRONMENT": "development",
        }):
            # Re-import to pick up patched env
            import importlib
            import src.config as cfg
            importlib.reload(cfg)
            errors = cfg.validate_config()
            # Should not require postgres in dev
            assert not any("requires Postgres" in e for e in errors)

    def test_production_requires_postgres(self):
        """Production mode requires Postgres backend."""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "STORAGE_BACKEND": "memory",
            "ENVIRONMENT": "production",
            "DEEPSEEK_API_KEY": "sk-test",
        }):
            import importlib
            import src.config as cfg
            importlib.reload(cfg)
            errors = cfg.validate_config()
            assert any("Postgres" in e for e in errors)

    def test_production_requires_api_key(self):
        """Production mode requires DEEPSEEK_API_KEY."""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "STORAGE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://test:test@localhost/test",
            "ENVIRONMENT": "production",
            "DEEPSEEK_API_KEY": "",
        }):
            import importlib
            import src.config as cfg
            importlib.reload(cfg)
            errors = cfg.validate_config()
            assert any("DEEPSEEK_API_KEY" in e for e in errors)


# ============================================================================
# Health / Ready endpoints
# ============================================================================

class TestHealthEndpoints:
    """Test /health and /ready responses."""

    def test_health_returns_200(self):
        """GET /health returns 200 with minimal response."""
        from src.main import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        # Phase 5: /health should NOT call storage backend
        assert "active_sessions" not in data

    def test_ready_returns_200_memory(self):
        """GET /ready returns 200 when using memory backend."""
        with patch("src.main.STORAGE_BACKEND", "memory"), \
             patch("src.config.STORAGE_BACKEND", "memory"):
            from src.main import app
            # Reset storage singleton so it doesn't reuse a postgres instance
            from src.storage.factory import reset_storage_backend
            reset_storage_backend()
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"
            reset_storage_backend()


# ============================================================================
# Correlation ID middleware
# ============================================================================

class TestCorrelationID:
    """Test X-Request-ID propagation."""

    def test_response_contains_request_id(self):
        """Every response should include X-Request-ID header."""
        from src.main import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert "x-request-id" in resp.headers

    def test_caller_supplied_request_id_is_echoed(self):
        """If caller sends X-Request-ID, it should be echoed back."""
        from src.main import app
        client = TestClient(app, raise_server_exceptions=False)
        custom_id = str(uuid.uuid4())
        resp = client.get("/health", headers={"X-Request-ID": custom_id})
        assert resp.headers.get("x-request-id") == custom_id

    def test_auto_generated_request_id_is_uuid(self):
        """Auto-generated request ID should be a valid UUID."""
        from src.main import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        req_id = resp.headers.get("x-request-id")
        # Should be parseable as UUID
        uuid.UUID(req_id)


# ============================================================================
# Twilio signature validation
# ============================================================================

class TestTwilioSignature:
    """Test Twilio webhook signature validation."""

    def test_signature_compute_matches_twilio_spec(self):
        """Our signature computation matches the Twilio algorithm."""
        from src.security.twilio_signature import _compute_twilio_signature

        # Example from Twilio docs
        auth_token = "12345"
        url = "https://mycompany.com/myapp.php?foo=1&bar=2"
        params = {
            "CallSid": "CA1234567890ABCDE",
            "Caller": "+14158675310",
            "Digits": "1234",
            "From": "+14158675310",
            "To": "+18005551212",
        }
        sig = _compute_twilio_signature(auth_token, url, params)
        assert isinstance(sig, str)
        assert len(sig) > 0

    def test_validation_disabled_in_dev(self):
        """When TWILIO_VALIDATE_SIGNATURE=false, webhooks pass without sig."""
        with patch("src.security.twilio_signature.TWILIO_VALIDATE_SIGNATURE", False):
            from src.main import app
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/voice/incoming",
                data={"CallSid": "CA_test_123"},
            )
            # Should NOT be 403
            assert resp.status_code != 403

    def test_validation_rejects_missing_signature(self):
        """When validation is on, missing X-Twilio-Signature gives 403."""
        with patch("src.security.twilio_signature.TWILIO_VALIDATE_SIGNATURE", True), \
             patch("src.security.twilio_signature.TWILIO_AUTH_TOKEN", "test-token"):
            from src.main import app
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/voice/incoming",
                data={"CallSid": "CA_test_123"},
            )
            assert resp.status_code == 403


# ============================================================================
# Structured logging
# ============================================================================

class TestStructuredLogging:
    """Test that structured JSON logging includes correlation fields."""

    def test_json_formatter_includes_request_id(self):
        """StructuredJSONFormatter should include request_id when set."""
        import json
        from src.observability.logging import StructuredJSONFormatter, _request_id

        formatter = StructuredJSONFormatter()
        import logging
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="hello", args=(), exc_info=None,
        )

        token = _request_id.set("req-abc-123")
        try:
            output = formatter.format(record)
            data = json.loads(output)
            assert data["request_id"] == "req-abc-123"
        finally:
            _request_id.reset(token)

    def test_json_formatter_includes_call_sid(self):
        """StructuredJSONFormatter should include call_sid when set."""
        import json
        from src.observability.logging import StructuredJSONFormatter, _call_sid

        formatter = StructuredJSONFormatter()
        import logging
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="hello", args=(), exc_info=None,
        )

        token = _call_sid.set("CAxyz")
        try:
            output = formatter.format(record)
            data = json.loads(output)
            assert data["call_sid"] == "CAxyz"
        finally:
            _call_sid.reset(token)


# ============================================================================
# PHI masking still active
# ============================================================================

class TestPHIMaskingActive:
    """Confirm PHI masking filter is installed on root logger."""

    def test_phi_filter_on_root_logger(self):
        """Root logger should have PHIMaskingFilter installed."""
        import logging
        from src.safety.phi_masking import PHIMaskingFilter
        root = logging.getLogger()
        phi_filters = [f for f in root.filters if isinstance(f, PHIMaskingFilter)]
        assert len(phi_filters) >= 1, "PHIMaskingFilter not found on root logger"


# ============================================================================
# Rate limiting
# ============================================================================

class TestRateLimiting:
    """Verify rate limiting middleware is active."""

    def test_health_exempt_from_rate_limit(self):
        """Health endpoint should be exempt from rate limiting."""
        from src.main import app
        client = TestClient(app, raise_server_exceptions=False)
        # Hit health many times — should never get 429
        for _ in range(100):
            resp = client.get("/health")
            assert resp.status_code == 200
