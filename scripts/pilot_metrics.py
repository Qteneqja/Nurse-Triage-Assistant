"""Birchwood pilot success metrics + cost estimates from stored data (PR 5).

Every pilot success metric in docs/pilot/BIRCHWOOD_SUCCESS_METRICS.md is
computed by this script from the live configured storage backend — no
manual spreadsheet work during the pilot.

Usage:
    python -m scripts.pilot_metrics                 # metrics, text output
    python -m scripts.pilot_metrics --json          # machine-readable
    python -m scripts.pilot_metrics --costs \
        --twilio-per-minute 0.0140 \
        --tts-per-million-chars 16.0 \
        --infra-per-day 3.50
    # On staging:
    #   az containerapp exec -n nurse-triage-api -g nurse-triage-rg \
    #     --command "python -m scripts.pilot_metrics"
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from typing import Any

BIRCHWOOD_WORKFLOW_ID = "birchwood_collision_intake_v1"


def _record_of(session: Any) -> dict[str, Any]:
    final = session.channel_metadata.get("workflow_final_result") or {}
    record = (final.get("structured_output") or {}).get("intake_record")
    return record if isinstance(record, dict) else {}


def _duration_seconds(session: Any) -> float | None:
    """Call duration: session start to the last conversation turn."""
    if not session.conversation:
        return None
    last = session.conversation[-1]
    last_ts = getattr(last, "timestamp", None)
    start = session.created_at
    if not isinstance(last_ts, datetime) or not isinstance(start, datetime):
        return None
    if last_ts.tzinfo is None and start.tzinfo is not None:
        last_ts = last_ts.replace(tzinfo=start.tzinfo)
    if start.tzinfo is None and last_ts.tzinfo is not None:
        start = start.replace(tzinfo=last_ts.tzinfo)
    seconds = (last_ts - start).total_seconds()
    return seconds if seconds >= 0 else None


def _tts_characters(session: Any) -> int:
    """Characters of assistant speech — the Azure TTS billing unit."""
    return sum(
        len(turn.text or "")
        for turn in session.conversation
        if getattr(turn, "role", "") == "assistant"
    )


def compute_metrics(sessions: list[Any], repo: Any | None = None) -> dict[str, Any]:
    """Compute the Birchwood pilot metrics over *sessions*."""
    total = len(sessions)
    finalized = [s for s in sessions if s.is_finalized]
    outcomes: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    durations: list[float] = []
    tts_chars: list[int] = []
    completed_without_rescue = 0
    callback_needed = 0
    injury_flagged_ids: list[str] = []

    for session in sessions:
        record = _record_of(session)
        outcome = str(
            record.get("recommended_routing") or record.get("outcome") or "UNKNOWN"
        )
        outcomes[outcome] += 1
        for flag in record.get("flags") or []:
            flags[str(flag)] += 1
        if record.get("callback_needed"):
            callback_needed += 1
        if "injuries_reported" in (record.get("flags") or []):
            injury_flagged_ids.append(session.session_id)
        if (
            session.is_finalized
            and outcome == "COMPLETED_INTAKE"
            and not record.get("human_review_required")
            and "readback_correction" not in (record.get("flags") or [])
        ):
            completed_without_rescue += 1
        duration = _duration_seconds(session)
        if duration is not None:
            durations.append(duration)
        tts_chars.append(_tts_characters(session))
        if repo is not None:
            try:
                events = repo.get_record_status_events(session.session_id)
            except Exception:
                events = []
            statuses[events[0]["status"] if events else "new/derived"] += 1

    drop_offs = total - len(finalized)
    return {
        "total_calls": total,
        "finalized_calls": len(finalized),
        "outcomes": dict(outcomes),
        "flags": dict(flags),
        "record_statuses": dict(statuses),
        # Success metrics (docs/pilot/BIRCHWOOD_SUCCESS_METRICS.md)
        "completed_without_rescue_pct": (
            round(100.0 * completed_without_rescue / total, 1) if total else None
        ),
        "drop_off_pct": round(100.0 * drop_offs / total, 1) if total else None,
        "callback_needed_pct": (
            round(100.0 * callback_needed / total, 1) if total else None
        ),
        "injury_flagged_count": len(injury_flagged_ids),
        "injury_flagged_session_ids": injury_flagged_ids,
        "avg_call_duration_seconds": (
            round(sum(durations) / len(durations), 1) if durations else None
        ),
        "avg_tts_characters": (
            round(sum(tts_chars) / len(tts_chars)) if tts_chars else None
        ),
    }


def compute_costs(
    metrics: dict[str, Any],
    *,
    twilio_per_minute: float,
    tts_per_million_chars: float,
    infra_per_day: float,
    calls_per_day: float,
) -> dict[str, Any]:
    """Per-call and monthly cost estimate.

    Birchwood's collision workflow is fully deterministic — no LLM calls in
    the live flow — so per-call cost is Twilio voice minutes + Azure TTS
    characters, plus amortized infrastructure.
    """
    duration_min = (metrics.get("avg_call_duration_seconds") or 0) / 60.0
    tts_chars = metrics.get("avg_tts_characters") or 0
    twilio_cost = duration_min * twilio_per_minute
    tts_cost = tts_chars * tts_per_million_chars / 1_000_000.0
    variable_per_call = twilio_cost + tts_cost
    infra_per_call = (infra_per_day / calls_per_day) if calls_per_day else None
    return {
        "assumptions": {
            "twilio_per_minute_usd": twilio_per_minute,
            "tts_per_million_chars_usd": tts_per_million_chars,
            "infra_per_day_usd": infra_per_day,
            "calls_per_day": calls_per_day,
            "llm_per_call_usd": 0.0,
            "llm_note": "Birchwood collision flow is deterministic (no LLM)",
        },
        "avg_call_minutes": round(duration_min, 2),
        "twilio_per_call_usd": round(twilio_cost, 4),
        "tts_per_call_usd": round(tts_cost, 4),
        "variable_per_call_usd": round(variable_per_call, 4),
        "infra_per_call_usd": (
            round(infra_per_call, 4) if infra_per_call is not None else None
        ),
        "total_per_call_usd": (
            round(variable_per_call + infra_per_call, 4)
            if infra_per_call is not None
            else None
        ),
        "monthly_at_volume_usd": (
            round((variable_per_call * calls_per_day + infra_per_day) * 30.4, 2)
            if calls_per_day
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--costs", action="store_true")
    parser.add_argument("--twilio-per-minute", type=float, default=0.0140)
    parser.add_argument("--tts-per-million-chars", type=float, default=16.0)
    parser.add_argument("--infra-per-day", type=float, default=3.50)
    parser.add_argument("--calls-per-day", type=float, default=20.0)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    from src.storage.session_repository import get_session_repository

    repo = get_session_repository()
    sessions = repo.list_recent_sessions(
        limit=args.limit, vertical_key="automotive_collision"
    )
    metrics = compute_metrics(sessions, repo=repo)
    output: dict[str, Any] = {"metrics": metrics}
    if args.costs:
        output["costs"] = compute_costs(
            metrics,
            twilio_per_minute=args.twilio_per_minute,
            tts_per_million_chars=args.tts_per_million_chars,
            infra_per_day=args.infra_per_day,
            calls_per_day=args.calls_per_day,
        )

    if args.json:
        print(json.dumps(output, indent=2))
        return 0

    m = metrics
    print("Birchwood pilot metrics")
    print("=" * 50)
    print(f"Total calls               : {m['total_calls']}")
    print(f"Finalized                 : {m['finalized_calls']}")
    print(f"Completed without rescue  : {m['completed_without_rescue_pct']}%")
    print(f"Drop-off rate             : {m['drop_off_pct']}%")
    print(f"Callback-needed rate      : {m['callback_needed_pct']}%")
    print(f"Injury-flagged records    : {m['injury_flagged_count']}")
    print(f"Avg call duration (s)     : {m['avg_call_duration_seconds']}")
    print(f"Outcomes                  : {m['outcomes']}")
    print(f"Flags                     : {m['flags']}")
    print(f"Record statuses           : {m['record_statuses']}")
    if m["injury_flagged_session_ids"]:
        print("Injury sessions for manual correctness review:")
        for session_id in m["injury_flagged_session_ids"]:
            print(f"  - {session_id}")
    if args.costs:
        c = output["costs"]
        print()
        print("Cost estimate (fill assumptions from real invoices)")
        print("-" * 50)
        for key, value in c.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
