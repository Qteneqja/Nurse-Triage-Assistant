"""
Tests for Twilio Signature Verification & Health Endpoints
Purpose: Verify Twilio webhook security and operational health probes
Date Created: 2026-03-02
Step: Production Hardening — Step 2

Tests:
  1. Invalid signature in prod mode → 403, no session created
  2. Missing signature header in prod mode → 403, no session created
  3. Valid signature in prod mode → request proceeds (no 403)
  4. Missing signature in dev mode → 200 with warning log (bypass allowed)
  5. GET /health returns 200 with status "ok" — no Twilio signature required
  6. GET /ready returns 200 when DB is available (memory backend)
  7. GET /ready returns 503 when DB is unavailable (mock connection failure)
     Assert: response does NOT contain exception class names or stack traces
"""
import hashlib
import hmac
import logging
from base64 import b64encode
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helper: compute a valid Twilio signature for testing
# ---------------------------------------------------------------------------

def _make_twilio_signature(auth_token: str, url: str, params: dict) -> str:
    """Compute a valid Twilio X-Twilio-Signature for the given URL and POST params."""
    s = url
    for key in sorted(params.keys()):
        s += key + (params[key] or "")
    mac = hmac.new(auth_token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1)
    return b64encode(mac.digest()).decode("utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def _patch_config_prod():
    """Patch config to simulate production with Twilio signature validation enabled."""
    with patch("src.security.twilio_signature.TWILIO_VALIDATE_SIGNATURE", True), \
         patch("src.security.twilio_signature.TWILIO_AUTH_TOKEN", "test-auth-token-for-tests"), \
         patch("src.security.twilio_signature.TWILIO_WEBHOOK_BASE_URL", ""), \
         patch("src.security.twilio_signature.APP_ENV", "production"):
        yield


@pytest.fixture
def _patch_config_dev():
    """Patch config to simulate development with Twilio signature validation disabled."""
    with patch("src.security.twilio_signature.TWILIO_VALIDATE_SIGNATURE", False), \
         patch("src.security.twilio_signature.APP_ENV", "development"):
        yield


@pytest.fixture
def client():
    """Create a TestClient for the FastAPI app."""
    # Import app fresh to avoid module-level config caching issues
    from src.main import app
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Twilio Signature Tests
# ---------------------------------------------------------------------------

class TestTwilioSignatureEnforcement:
    """Tests for Twilio webhook signature validation."""

    def test_invalid_signature_prod_returns_403(self, client, _patch_config_prod):
        """Test 1: Invalid signature in prod mode → 403, no session created."""
        with patch("src.orchestrator.orchestrator.Orchestrator") as mock_orch:
            response = client.post(
                "/api/v1/voice/incoming",
                data={"CallSid": "CA123", "From": "+15551234567"},
                headers={"X-Twilio-Signature": "invalid-signature-here"},
            )
            assert response.status_code == 403
            body = response.json()
            assert "error" in body.get("detail", body)
            # Verify no session/orchestrator was invoked
            mock_orch.assert_not_called()

    def test_missing_signature_prod_returns_403(self, client, _patch_config_prod):
        """Test 2: Missing signature header in prod mode → 403, no session created."""
        with patch("src.orchestrator.orchestrator.Orchestrator") as mock_orch:
            response = client.post(
                "/api/v1/voice/incoming",
                data={"CallSid": "CA123", "From": "+15551234567"},
                # No X-Twilio-Signature header
            )
            assert response.status_code == 403
            body = response.json()
            assert "error" in body.get("detail", body)
            mock_orch.assert_not_called()

    def test_valid_signature_prod_proceeds(self, client, _patch_config_prod):
        """Test 3: Valid signature in prod mode → request is not rejected with 403."""
        # Compute a valid signature for the test URL and params
        test_url = "http://testserver/api/v1/voice/incoming"
        test_params = {"CallSid": "CA123", "From": "+15551234567"}
        valid_sig = _make_twilio_signature("test-auth-token-for-tests", test_url, test_params)

        response = client.post(
            "/api/v1/voice/incoming",
            data=test_params,
            headers={"X-Twilio-Signature": valid_sig},
        )
        # Should NOT be 403 — may be another status depending on route logic,
        # but the signature check itself must pass.
        assert response.status_code != 403

    def test_missing_signature_dev_bypasses(self, client, _patch_config_dev, caplog):
        """Test 4: Missing signature in dev mode → bypass allowed, warning logged."""
        with caplog.at_level(logging.WARNING):
            response = client.post(
                "/api/v1/voice/incoming",
                data={"CallSid": "CA123", "From": "+15551234567"},
                # No X-Twilio-Signature header — should be allowed in dev
            )
            # Should not be 403 — signature validation is skipped in dev
            assert response.status_code != 403
            # Verify warning was logged about skipping validation
            assert any("SKIPPED" in record.message or "skipped" in record.message.lower()
                      for record in caplog.records if "TwilioSig" in record.message)


# ---------------------------------------------------------------------------
# Health Endpoint Tests
# ---------------------------------------------------------------------------

class TestHealthEndpoints:
    """Tests for /health and /ready operational endpoints."""

    def test_health_returns_200_with_ok(self, client):
        """Test 5: GET /health returns 200 with status 'ok' — no Twilio signature required."""
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "timestamp" in body

    def test_ready_returns_200_when_db_available(self, client):
        """Test 6: GET /ready returns 200 when storage backend is available."""
        with patch("src.main.STORAGE_BACKEND", "memory"):
            response = client.get("/ready")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "ready"

    def test_ready_returns_503_on_db_failure_no_leak(self, client):
        """Test 7: GET /ready returns 503 when DB is unavailable.
        Assert: response does NOT contain exception class names or stack traces.
        """
        def _raise_connection_error():
            raise ConnectionError("could not connect to server: Connection refused")

        with patch("src.main.STORAGE_BACKEND", "postgres"), \
             patch("src.main.APP_ENV", "production"), \
             patch("src.main.get_storage_backend", side_effect=ConnectionError(
                 "could not connect to server: Connection refused"
             )):
            response = client.get("/ready")
            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "not_ready"
            assert body["database"] == "unavailable"
            # Must NOT contain exception class names or stack traces in prod
            body_str = str(body)
            assert "ConnectionError" not in body_str
            assert "Traceback" not in body_str
            assert "could not connect" not in body_str
            # Should NOT have a "detail" key in production
            assert "detail" not in body
