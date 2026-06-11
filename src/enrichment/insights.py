"""Feature 6 — aggregate insights across enriched calls (ENRICH_INSIGHTS).

Honest by construction: below ENRICH_INSIGHTS_MIN_CALLS the view reports
"insufficient data" instead of dressing noise up as signal, and every
response carries the sample size. The aggregates themselves are
DETERMINISTIC (no tokens, no PII leaves the system); the optional LLM
commentary sees only these aggregate numbers, never call content.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from pydantic import BaseModel

import src.config as config

logger = logging.getLogger(__name__)


class InsightsCommentary(BaseModel):
    observations: list[str]
    caveats: list[str]


def compute_insights(sessions: list[Any]) -> dict[str, Any]:
    """Deterministic aggregates over finalized Birchwood sessions."""
    from src.pilot.metrics import compute_metrics, _record_of

    min_calls = int(getattr(config, "ENRICH_INSIGHTS_MIN_CALLS", 30))
    sample = len(sessions)
    base = {
        "sample_size": sample,
        "min_calls_for_reliability": min_calls,
        "reliable": sample >= min_calls,
    }
    if sample == 0:
        return {**base, "status": "insufficient_data", "detail": "No calls yet."}

    metrics = compute_metrics(sessions)
    hours: Counter[int] = Counter()
    damage_terms: Counter[str] = Counter()
    drivable = {"yes": 0, "no": 0, "unknown": 0}
    with_claim = 0
    for session in sessions:
        if getattr(session.created_at, "hour", None) is not None:
            hours[session.created_at.hour] += 1
        record = _record_of(session)
        for term in str(record.get("damage_type") or "").split(","):
            term = term.strip().lower()
            if term:
                damage_terms[term] += 1
        is_drivable = record.get("is_drivable")
        drivable[
            "yes" if is_drivable else "no" if is_drivable is False else "unknown"
        ] += 1
        if record.get("claim_number"):
            with_claim += 1

    insights = {
        **base,
        "status": "ok" if sample >= min_calls else "insufficient_data",
        "detail": (
            None
            if sample >= min_calls
            else f"Only {sample} calls — {min_calls}+ needed for reliable insights."
        ),
        "peak_call_hours_utc": [
            {"hour": hour, "calls": count} for hour, count in hours.most_common(3)
        ],
        "common_damage_patterns": [
            {"pattern": term, "calls": count}
            for term, count in damage_terms.most_common(5)
        ],
        "drivable_vs_towed": drivable,
        "pct_with_claim_number": round(100.0 * with_claim / sample, 1),
        "avg_intake_duration_seconds": metrics["avg_call_duration_seconds"],
        "completion_without_rescue_pct": metrics["completed_without_rescue_pct"],
        "drop_off_pct": metrics["drop_off_pct"],
        "outcomes": metrics["outcomes"],
    }
    return insights


async def insights_commentary(aggregates: dict[str, Any]) -> dict[str, Any] | None:
    """Optional analyst commentary over AGGREGATE NUMBERS only (no PII)."""
    if not aggregates.get("reliable"):
        return None
    try:
        from src.enrichment.provider import get_enrichment_provider

        provider = get_enrichment_provider()
        safe = {k: v for k, v in aggregates.items() if k != "detail"}
        output = await provider.call(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a data analyst for a collision intake line. "
                        "You see ONLY aggregate statistics — no call content. "
                        "Respond ONLY with JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Give 2-4 concise business observations and any "
                        "statistical caveats for these pilot aggregates:\n"
                        f"{safe}\n\nJSON keys: observations, caveats."
                    ),
                },
            ],
            InsightsCommentary,
            max_tokens=400,
        )
        return output.model_dump(mode="json")
    except Exception:
        logger.warning("[ENRICHMENT] Insights commentary failed (fail closed)")
        return None
