"""
Phase 3 — Observability Tests

Tests for structured logging, metrics counters/histograms, and
health/ready/metrics API endpoints.
"""
import json
import logging
import time
import pytest
from unittest.mock import patch, MagicMock

from src.observability.metrics import MetricsRegistry, get_metrics, reset_metrics
from src.observability.logging import (
    StructuredJSONFormatter,
    set_log_context,
    clear_log_context,
    configure_structured_logging,
)


# ---------------------------------------------------------------------------
# Metrics Registry
# ---------------------------------------------------------------------------

class TestMetricsRegistry:
    """Test the custom metrics registry."""

    def setup_method(self):
        reset_metrics()
        self.metrics = get_metrics()

    def test_singleton(self):
        """get_metrics returns the same instance."""
        m2 = get_metrics()
        assert self.metrics is m2

    def test_reset(self):
        """reset_metrics creates a new instance."""
        self.metrics.triage_sessions_total.inc()
        old_id = id(self.metrics)
        reset_metrics()
        new_m = get_metrics()
        assert id(new_m) != old_id
        assert new_m.triage_sessions_total.value == 0

    def test_counter_inc(self):
        """Counter increments correctly."""
        self.metrics.triage_sessions_total.inc()
        self.metrics.triage_sessions_total.inc()
        assert self.metrics.triage_sessions_total.value == 2

    def test_counter_inc_by(self):
        """Counter increments by a specific value."""
        self.metrics.triage_sessions_total.inc(5)
        assert self.metrics.triage_sessions_total.value == 5

    def test_counter_starts_at_zero(self):
        """Counters start at zero."""
        assert self.metrics.triage_sessions_total.value == 0
        assert self.metrics.red_flag_triggers_total.value == 0

    def test_histogram_observe(self):
        """Histogram records observations correctly."""
        self.metrics.confidence_score.observe(0.85)
        self.metrics.confidence_score.observe(0.90)
        self.metrics.confidence_score.observe(0.95)
        assert self.metrics.confidence_score.count == 3
        assert abs(self.metrics.confidence_score.sum - 2.70) < 0.001

    def test_histogram_empty(self):
        """Empty histogram has zero count."""
        assert self.metrics.confidence_score.count == 0
        assert self.metrics.confidence_score.sum == 0

    def test_to_dict(self):
        """to_dict returns all counters and histograms."""
        self.metrics.triage_sessions_total.inc(3)
        self.metrics.turn_latency_ms.observe(120.0)
        snap = self.metrics.to_dict()
        assert "counters" in snap
        assert "histograms" in snap
        assert snap["counters"]["triage_sessions_total"] == 3
        assert snap["histograms"]["turn_latency_ms"]["count"] == 1

    def test_all_known_counters(self):
        """All declared counters exist in to_dict output."""
        snap = self.metrics.to_dict()
        expected_counters = [
            "triage_sessions_total",
            "triage_escalations_total",
            "red_flag_triggers_total",
            "llm_timeouts_total",
            "json_repairs_total",
            "post_check_violations_total",
            "retriever_hits_total",
        ]
        for c in expected_counters:
            assert c in snap["counters"]

    def test_all_known_histograms(self):
        """All declared histograms exist in to_dict output."""
        snap = self.metrics.to_dict()
        expected_histograms = ["confidence_score", "turn_latency_ms"]
        for h in expected_histograms:
            assert h in snap["histograms"]


# ---------------------------------------------------------------------------
# Structured Logging
# ---------------------------------------------------------------------------

class TestStructuredLogging:
    """Test the structured JSON log formatter and context vars."""

    def test_json_formatter_output(self):
        """Formatter produces valid JSON."""
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello world",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "Hello world"
        assert data["level"] == "INFO"
        assert "timestamp" in data

    def test_context_vars_in_log(self):
        """Context variables are included in log output."""
        formatter = StructuredJSONFormatter()
        set_log_context(session_id="SID-123", turn_index=5)
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test log",
                args=None,
                exc_info=None,
            )
            output = formatter.format(record)
            data = json.loads(output)
            assert data.get("session_id") == "SID-123"
            assert data.get("turn_index") == 5
        finally:
            clear_log_context()

    def test_clear_context(self):
        """clear_log_context removes context variables."""
        set_log_context(session_id="SID-999")
        clear_log_context()
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="After clear",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data.get("session_id") is None

    def test_configure_structured_logging(self):
        """configure_structured_logging attaches handler to root logger."""
        configure_structured_logging(level="WARNING")
        root = logging.getLogger()
        # Should have at least one handler with StructuredJSONFormatter
        has_structured = any(
            isinstance(h.formatter, StructuredJSONFormatter) for h in root.handlers
        )
        assert has_structured


# ---------------------------------------------------------------------------
# API Endpoints (health, ready, metrics)
# ---------------------------------------------------------------------------

class TestHealthEndpoints:
    """Test /health, /ready, /metrics endpoints via TestClient."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        """Reset metrics and create fresh test client."""
        reset_metrics()
        mock_backend = MagicMock()
        mock_backend.get_active_session_count.return_value = 3
        # Patch config to avoid startup failures in test
        with patch("src.main.require_valid_config"), \
             patch("src.main.validate_approved_protocols_exist"), \
             patch("src.main.get_storage_backend", return_value=mock_backend):
            from src.main import app
            from starlette.testclient import TestClient
            self.client = TestClient(app, raise_server_exceptions=False)
            yield

    def test_health_ok(self):
        """GET /health returns 200 — pure liveness probe (Phase 5)."""
        resp = self.client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        # Phase 5: /health is a lightweight liveness probe; no storage calls
        # active_sessions and storage_backend moved to /ready and /metrics

    def test_metrics_endpoint(self):
        """GET /metrics returns counters and histograms."""
        m = get_metrics()
        m.triage_sessions_total.inc(7)
        resp = self.client.get("/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "counters" in data
        assert data["counters"]["triage_sessions_total"] == 7


# ---------------------------------------------------------------------------
# Security Middleware — Rate Limiter
# ---------------------------------------------------------------------------

class TestSecurityMiddleware:
    """Test the _RateLimitStore directly."""

    def test_rate_limiter_allows_normal_traffic(self):
        """Normal traffic under rate limit is allowed."""
        from src.security.middleware import _RateLimitStore
        store = _RateLimitStore("100/minute")
        assert store.is_allowed("127.0.0.1") is True

    def test_rate_limiter_blocks_excess(self):
        """Traffic exceeding rate limit is blocked."""
        from src.security.middleware import _RateLimitStore
        store = _RateLimitStore("2/minute")
        assert store.is_allowed("10.0.0.1") is True
        assert store.is_allowed("10.0.0.1") is True
        assert store.is_allowed("10.0.0.1") is False  # 3rd request blocked

    def test_rate_limiter_different_ips_independent(self):
        """Different IPs have independent rate limits."""
        from src.security.middleware import _RateLimitStore
        store = _RateLimitStore("1/minute")
        assert store.is_allowed("10.0.0.1") is True
        assert store.is_allowed("10.0.0.2") is True
        assert store.is_allowed("10.0.0.1") is False
        assert store.is_allowed("10.0.0.2") is False

    def test_rate_limiter_concurrent_access(self):
        """Rate limiter remains correct under concurrent thread access."""
        import threading
        from src.security.middleware import _RateLimitStore
        store = _RateLimitStore("50/minute")
        results: list[bool] = []
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()  # synchronise start
            for _ in range(10):
                results.append(store.is_allowed("shared-ip"))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 50 should be allowed, rest denied (100 total attempts)
        assert sum(results) == 50
        assert len(results) == 100


# ---------------------------------------------------------------------------
# Client IP Extraction (X-Forwarded-For)
# ---------------------------------------------------------------------------

class TestClientIPExtraction:
    """Test _extract_client_ip with and without proxy headers."""

    def test_no_proxy_uses_client_host(self):
        """Without TRUST_PROXY_HEADERS, uses request.client.host."""
        from src.security.middleware import _extract_client_ip
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers = {}
        with patch("src.security.middleware.TRUST_PROXY_HEADERS", False):
            assert _extract_client_ip(request) == "10.0.0.1"

    def test_proxy_xff_public_ip(self):
        """With TRUST_PROXY_HEADERS, picks first public IP from XFF."""
        from src.security.middleware import _extract_client_ip
        request = MagicMock()
        request.client.host = "172.17.0.1"
        request.headers = {"x-forwarded-for": "203.0.113.50, 10.0.0.1, 172.17.0.1"}
        with patch("src.security.middleware.TRUST_PROXY_HEADERS", True):
            assert _extract_client_ip(request) == "203.0.113.50"

    def test_proxy_xff_all_private_uses_first(self):
        """When all XFF IPs are private, use first entry."""
        from src.security.middleware import _extract_client_ip
        request = MagicMock()
        request.client.host = "172.17.0.1"
        request.headers = {"x-forwarded-for": "10.0.0.1, 192.168.1.1"}
        with patch("src.security.middleware.TRUST_PROXY_HEADERS", True):
            assert _extract_client_ip(request) == "10.0.0.1"

    def test_proxy_disabled_ignores_xff(self):
        """When TRUST_PROXY_HEADERS=False, XFF is ignored."""
        from src.security.middleware import _extract_client_ip
        request = MagicMock()
        request.client.host = "127.0.0.1"
        request.headers = {"x-forwarded-for": "203.0.113.50"}
        with patch("src.security.middleware.TRUST_PROXY_HEADERS", False):
            assert _extract_client_ip(request) == "127.0.0.1"


# ---------------------------------------------------------------------------
# Active Session Count (public API)
# ---------------------------------------------------------------------------

class TestActiveSessionCount:
    """Test get_active_session_count on both storage backends."""

    def test_memory_storage_count(self):
        """InMemoryOrchestratorStorage tracks active session count."""
        from src.storage.memory import InMemoryOrchestratorStorage
        store = InMemoryOrchestratorStorage()
        assert store.get_active_session_count() == 0
        s1 = store.create_session()
        assert store.get_active_session_count() == 1
        s2 = store.create_session()
        assert store.get_active_session_count() == 2
        store.delete_session(s1.session_id)
        assert store.get_active_session_count() == 1
