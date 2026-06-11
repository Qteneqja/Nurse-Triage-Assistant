"""Birchwood pilot success metrics + cost estimates from stored data (PR 5).

Every pilot success metric in docs/pilot/BIRCHWOOD_SUCCESS_METRICS.md is
computed by this script from the live configured storage backend — no
manual spreadsheet work during the pilot.

The computation itself lives in src/pilot/metrics.py (so production code
can import it — the Docker image ships src/ but not scripts/); this is
the operator CLI.

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
from typing import Any

from src.pilot.metrics import (  # noqa: F401  (re-exported for importers)
    BIRCHWOOD_WORKFLOW_ID,
    _duration_seconds,
    _record_of,
    _tts_characters,
    compute_costs,
    compute_metrics,
)


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
