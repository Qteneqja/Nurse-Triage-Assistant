"""
Metrics Layer — Phase 3

Simple, dependency-free metrics counters and histograms.
Exposes /metrics-compatible data and an in-process query API.

No Prometheus client dependency — uses plain Python for pilot simplicity.
Can be swapped for prometheus_client later if needed.
"""

from __future__ import annotations

import math
import threading
from typing import Dict, Optional


class _Counter:
    """Thread-safe counter."""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self._value: float = 0.0
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value += amount

    @property
    def value(self) -> float:
        with self._lock:
            return self._value


class _Histogram:
    """Thread-safe histogram with configurable buckets."""

    DEFAULT_BUCKETS = (
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        float("inf"),
    )

    def __init__(
        self, name: str, description: str, buckets: tuple | None = None
    ) -> None:
        self.name = name
        self.description = description
        self._buckets = buckets or self.DEFAULT_BUCKETS
        self._counts: Dict[float, int] = {b: 0 for b in self._buckets}
        self._sum: float = 0.0
        self._count: int = 0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._count += 1
            for b in self._buckets:
                if value <= b:
                    self._counts[b] += 1

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    @property
    def sum(self) -> float:
        with self._lock:
            return self._sum

    def snapshot(self) -> dict:
        with self._lock:
            # Replace inf with string "+Inf" for JSON compatibility
            safe_buckets = {
                ("+Inf" if math.isinf(k) else k): v for k, v in self._counts.items()
            }
            return {
                "count": self._count,
                "sum": self._sum,
                "buckets": safe_buckets,
            }


class MetricsRegistry:
    """Central registry for all application metrics."""

    def __init__(self) -> None:
        # Counters
        self.triage_sessions_total = _Counter(
            "triage_sessions_total",
            "Total number of triage sessions created",
        )
        self.triage_escalations_total = _Counter(
            "triage_escalations_total",
            "Total number of escalations triggered",
        )
        self.red_flag_triggers_total = _Counter(
            "red_flag_triggers_total",
            "Total number of red flag triggers",
        )
        self.llm_timeouts_total = _Counter(
            "llm_timeouts_total",
            "Total number of LLM timeout/failures",
        )
        self.json_repairs_total = _Counter(
            "json_repairs_total",
            "Total number of JSON repair attempts",
        )
        self.post_check_violations_total = _Counter(
            "post_check_violations_total",
            "Total number of post-check safety gate violations",
        )
        self.retriever_hits_total = _Counter(
            "retriever_hits_total",
            "Total number of protocol retriever hits",
        )

        # Histograms
        self.confidence_score = _Histogram(
            "confidence_score",
            "Distribution of confidence scores",
            buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, float("inf")),
        )
        self.turn_latency_ms = _Histogram(
            "turn_latency_ms",
            "Distribution of turn processing latency in milliseconds",
            buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, float("inf")),
        )

    def to_dict(self) -> dict:
        """Export all metrics as a plain dict (for /metrics endpoint)."""
        return {
            "counters": {
                "triage_sessions_total": self.triage_sessions_total.value,
                "triage_escalations_total": self.triage_escalations_total.value,
                "red_flag_triggers_total": self.red_flag_triggers_total.value,
                "llm_timeouts_total": self.llm_timeouts_total.value,
                "json_repairs_total": self.json_repairs_total.value,
                "post_check_violations_total": self.post_check_violations_total.value,
                "retriever_hits_total": self.retriever_hits_total.value,
            },
            "histograms": {
                "confidence_score": self.confidence_score.snapshot(),
                "turn_latency_ms": self.turn_latency_ms.snapshot(),
            },
        }


# Module-level singleton
_metrics: Optional[MetricsRegistry] = None


def get_metrics() -> MetricsRegistry:
    """Get or create the singleton MetricsRegistry."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsRegistry()
    return _metrics


def reset_metrics() -> None:
    """Reset metrics singleton. Used in testing."""
    global _metrics
    _metrics = None
