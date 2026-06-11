"""Seed the dashboard with Birchwood demo intake records (PR 4).

Runs the offline conversation simulator scenarios against the LIVE
configured storage backend (memory in dev, Postgres in compose/staging),
then applies a couple of status changes so the audit trail has content.

Usage (local dev):
    python -m scripts.seed_dashboard_demo

Usage (docker compose):
    docker compose exec api python -m scripts.seed_dashboard_demo

Then open /dashboard/records. Demo data only — never run against a live
pilot database.
"""

from __future__ import annotations

import sys
import uuid

from scripts.simulate_birchwood_call import SCENARIOS, simulate


def main() -> int:
    run_tag = uuid.uuid4().hex[:8].upper()
    print(f"Seeding dashboard demo records (run {run_tag})...")
    seeded: list[dict] = []
    for name in sorted(SCENARIOS):
        result = simulate(
            name,
            patch_storage=False,  # use the live configured backend
            call_sid=f"CA-SEED-{run_tag}-{name.upper()}",
        )
        seeded.append(result)
        print(
            f"  {name}: outcome={result['outcome']} "
            f"flags={','.join(result['flags']) or 'none'}"
        )

    # Exercise the status workflow so the audit log shows real history.
    from src.storage.session_repository import get_session_repository

    repo = get_session_repository()
    sessions = repo.list_recent_sessions(limit=20, vertical_key="automotive_collision")
    demo_sessions = [
        s for s in sessions if (s.call_sid or "").startswith(f"CA-SEED-{run_tag}")
    ]
    if demo_sessions:
        first = demo_sessions[-1]
        repo.append_record_status_event(
            first.session_id, "contacted", "demo.seeder", "Left a voicemail."
        )
        repo.append_record_status_event(
            first.session_id, "scheduled", "demo.seeder", "Booked for Thursday 9am."
        )
        print(
            f"  status demo: {first.session_id} -> contacted -> scheduled "
            "(see status history)"
        )

    print()
    print(f"Seeded {len(seeded)} records. Open the dashboard:")
    print("  http://localhost:8000/dashboard/records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
