"""Curated Birchwood Collision demo records for the pitch dashboard.

Loads 36 realistic, fully-populated synthetic collision intakes into the LIVE
configured storage backend so every panel of /dashboard/birchwood is non-zero
and credible: believable Manitoba names, 204-555-01xx callback numbers, real
vehicle combos, normalized damage text, an MPI-heavy insurer mix, ~70/30
drivable-vs-towed, several records landing today/this week, and 3
injury-flagged records that pin to the top with the safety advisory.

Every record is synthetic (no real person's data), carries a demo_seed
marker, and is tagged with the CA-BWDEMO- call-sid prefix so the whole set
is separable and removable without touching real staging records. Transcripts
open with the real dedicated-line greeting (BIRCHWOOD_COLLISION_INTRO) — no
shared-number vertical menu, no other verticals.

Usage:
    python -m scripts.seed_birchwood_demo            # load
    python -m scripts.seed_birchwood_demo --remove   # remove all seeded rows

--remove also cleans records from the older CA-BWPITCH- seed batches.
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta

from src.orchestrator.schemas import ConversationTurn, OrchestratorSession
from src.storage.session_repository import get_session_repository
from src.verticals.automotive_collision.prompts import BIRCHWOOD_COLLISION_INTRO

SEED_CALL_SID_PREFIX = "CA-BWDEMO-"
LEGACY_SEED_PREFIXES = ("CA-BWPITCH-",)
WORKFLOW_ID = "birchwood_collision_intake_v1"
VERTICAL = "automotive_collision"
DEFAULT_COUNT = 36

# Deterministic output: the pitch looks the same on every load.
_RNG = random.Random(20260612)

# Believable Manitoba-area names — all synthetic pairings, no real people.
_FIRST = [
    "Mara",
    "Devon",
    "Lena",
    "Carter",
    "Sofia",
    "Brent",
    "Priya",
    "Marc",
    "Jolene",
    "Tyler",
    "Renee",
    "Owen",
    "Kayla",
    "Ethan",
    "Tessa",
    "Cole",
    "Angela",
    "Dustin",
    "Maya",
    "Liam",
    "Chantal",
    "Noah",
    "Brielle",
    "Evan",
]
_LAST = [
    "Friesen",
    "Penner",
    "Sawatzky",
    "Dyck",
    "Hiebert",
    "Reimer",
    "Klassen",
    "Wiebe",
    "Thiessen",
    "Lavallee",
    "Beaulieu",
    "Santos",
    "Reyes",
    "Sinclair",
    "McKay",
    "Chartrand",
    "Krahn",
    "Zacharias",
    "Peters",
    "Dela Cruz",
    "Funk",
    "Toews",
    "Desjarlais",
    "Bergen",
]

_VEHICLES = [
    (2021, "Toyota", "Corolla"),
    (2019, "Honda", "Civic"),
    (2022, "Ford", "F-150"),
    (2018, "Mazda", "CX-5"),
    (2023, "Hyundai", "Tucson"),
    (2017, "Chevrolet", "Equinox"),
    (2020, "Kia", "Sportage"),
    (2016, "Dodge", "Grand Caravan"),
    (2022, "Subaru", "Outback"),
    (2021, "Nissan", "Rogue"),
    (2024, "Toyota", "RAV4"),
    (2015, "Honda", "CR-V"),
    (2023, "GMC", "Sierra"),
    (2019, "Volkswagen", "Tiguan"),
    (2020, "Jeep", "Wrangler"),
    (2022, "BMW", "X3"),
    (2018, "Ford", "Escape"),
    (2021, "Ram", "1500"),
]

# (normalized damage text, area bucket, glass_only, drivable-likely)
_DAMAGE = [
    ("Front bumper and hood", "front", False, True),
    ("Front end, radiator pushed in", "front", False, False),
    ("Rear bumper and trunk lid", "rear", False, True),
    ("Rear quarter panel", "rear", False, True),
    ("Driver-side door and fender", "side", False, True),
    ("Both passenger-side doors", "side", False, True),
    ("Front end and driver side", "multi", False, False),
    ("Rear bumper and passenger side", "multi", False, True),
    ("Windshield cracked", "glass", True, True),
    ("Hood, windshield, and roof edge", "multi", False, False),
]

# Manitoba mix: MPI is the provincial insurer, so it dominates.
_INSURERS = [
    "MPI",
    "MPI",
    "MPI",
    "MPI",
    "MPI",
    "MPI",
    "MPI",
    "Wawanesa",
    "Wawanesa",
    "Intact",
    "Aviva",
    "SGI",
]

_LOCATIONS = [
    "Pembina and Stafford",
    "Portage and Main",
    "Kenaston and McGillivray",
    "Regent and Lagimodiere",
    "Henderson and Chief Peguis",
    "Route 90 near Ness",
    "McPhillips and Leila",
    "Bishop Grandin and St. Mary's",
    "Osborne Village",
    "Corydon and Stafford",
    "Inkster and Keewatin",
    "Fermor and St. Anne's",
]

_OTHER_PARTIES = [
    "another vehicle involved",
    "two-vehicle collision",
    "hit a parked car",
    "hit and run - other driver left",
    "single vehicle, hit a pole",
    "another vehicle involved",
]

# Weighted local call-hour distribution: morning and early-afternoon peaks.
_HOURS = [
    (8, 3),
    (9, 5),
    (10, 4),
    (11, 3),
    (12, 2),
    (13, 4),
    (14, 4),
    (15, 3),
    (16, 2),
    (17, 2),
    (18, 1),
]

# 3 injury-flagged records (escalated via derived status), 2 urgent transfers.
_INJURY_INDEXES = (2, 9, 17)
_TRANSFER_INDEXES = (5, 21)

_STATUS_PLAN = (
    ["new"] * 12 + ["contacted"] * 9 + ["scheduled"] * 8 + ["completed"] * 7
)  # cycles per-index


def _pick_hour() -> int:
    total = sum(w for _, w in _HOURS)
    roll = _RNG.uniform(0, total)
    acc = 0.0
    for hour, weight in _HOURS:
        acc += weight
        if roll <= acc:
            return hour
    return 10


def _days_ago(index: int) -> int:
    """Several records land today, ~a third this week, the rest over 30 days."""
    if index < 5:
        return 0
    if index < 12:
        return _RNG.randint(1, 6)
    return _RNG.randint(7, 29)


def _turns(
    name: str,
    vehicle: str,
    damage: str,
    drivable: bool,
    injured: bool,
    insurer: str | None,
) -> list[ConversationTurn]:
    drive_text = (
        "Yes, it still drives fine."
        if drivable
        else "No - it had to be towed, it's not drivable."
    )
    injury_text = (
        "My neck has been a bit sore since it happened."
        if injured
        else "No, nobody was hurt."
    )
    insurer_text = (
        f"I'm going through {insurer}." if insurer else "I'll be paying privately."
    )
    script = [
        # The dedicated Birchwood line opens with the real workflow greeting —
        # never the shared-number vertical menu.
        ("assistant", BIRCHWOOD_COLLISION_INTRO),
        (
            "caller",
            f"I was in an accident with my {vehicle}. The "
            f"{damage.lower()} took the worst of it.",
        ),
        (
            "assistant",
            "I'm sorry you're dealing with this - let's get your "
            "vehicle looked after. Before anything else - was "
            "anyone hurt, even a little?",
        ),
        ("caller", injury_text),
        ("assistant", "Thank you. And is your vehicle safe to drive right now?"),
        ("caller", drive_text),
        ("assistant", "And are you going through insurance for the repair?"),
        ("caller", insurer_text),
        (
            "assistant",
            "Last thing - what's the best name and number for "
            "your advisor to reach you?",
        ),
        ("caller", f"{name}, on my cell."),
    ]
    return [ConversationTurn(role=role, text=text) for role, text in script]


def _build_session(index: int, run_tag: str) -> tuple[OrchestratorSession, str]:
    """Return (session, target_status) for seeded record #index."""
    first = _FIRST[index % len(_FIRST)]
    last = _LAST[(index * 7 + index // len(_FIRST)) % len(_LAST)]
    name = f"{first} {last}"
    phone = f"+1204555{100 + index:04d}"  # 204-555-01xx — clearly synthetic

    year, make, model = _VEHICLES[index % len(_VEHICLES)]
    vehicle_label = f"{year} {make} {model}"
    damage, area, glass_only, drivable_likely = _DAMAGE[index % len(_DAMAGE)]
    drivable = drivable_likely if _RNG.random() < 0.85 else not drivable_likely

    injured = index in _INJURY_INDEXES
    transfer = index in _TRANSFER_INDEXES

    private_pay = index in (7, 19, 28)
    insurer = None if private_pay else _INSURERS[index % len(_INSURERS)]
    claim_missing = (not private_pay) and index % 6 == 3
    claim_number = (
        None
        if private_pay or claim_missing
        else f"{(insurer or 'CLM')[:3].upper()}-DEMO-{4000 + index}"
    )

    missing: list[str] = ["claim_number"] if claim_missing else []
    callback_needed = bool(missing)
    if injured:
        disposition = (
            "INCOMPLETE_CALLBACK_NEEDED" if callback_needed else "COMPLETED_INTAKE"
        )
    elif transfer:
        disposition = "TRANSFER_COLLISION_CENTER"
    elif callback_needed:
        disposition = "INCOMPLETE_CALLBACK_NEEDED"
    else:
        disposition = "COMPLETED_INTAKE"

    flags = ["injuries_reported" if injured else "injuries_denied"]
    if not drivable:
        flags.append("non_drivable_transfer")
    if private_pay:
        flags.append("private_pay")
    if callback_needed:
        flags.append("callback_needed")

    days_ago = _days_ago(index)
    hour = _pick_hour()
    now_local = datetime.now().astimezone()
    if days_ago == 0:  # never timestamp a "today" record in the future
        hour = min(hour, max(8, now_local.hour))
    created_local = now_local.replace(
        hour=hour, minute=_RNG.randint(0, 59), second=0, microsecond=0
    ) - timedelta(days=days_ago)
    created = created_local.astimezone(UTC)
    duration_s = _RNG.randint(95, 240) if not injured else _RNG.randint(150, 300)

    location = _LOCATIONS[index % len(_LOCATIONS)]
    other = _OTHER_PARTIES[index % len(_OTHER_PARTIES)]
    police = ["yes", "no", "pending"][index % 3]
    photos = "yes" if index % 3 != 1 else "no"
    incident_when = [
        "this morning",
        "yesterday afternoon",
        "last night",
        "two days ago",
        "earlier today",
    ][index % 5]

    narrative = (
        f"I was in a collision {incident_when} near {location}. "
        f"It's my {vehicle_label} - {damage.lower()} took the impact. "
        f"{'It had to be towed.' if not drivable else 'It still drives.'} "
        f"{'My neck has been sore since.' if injured else 'Nobody was hurt.'} "
        f"({other}; police report: {police}; photos: {photos}.) "
        f"[SYNTHETIC DEMO DATA]"
    )
    shop_summary = (
        f"SITUATION: Collision {incident_when} near {location}. "
        f"{'Injuries reported - advisory issued. ' if injured else 'No injuries reported. '}"
        f"Other parties: {other}. Police report: {police}. Photos: {photos}.\n"
        f"VEHICLE: {vehicle_label}. Drivable: {'yes' if drivable else 'NO - towed'}. "
        f"Damage: {damage}. Rebuilt/salvage: no.\n"
        f"CUSTOMER: {name}, callback {phone}, "
        f"{'private pay' if private_pay else f'insurance via {insurer}'}"
        f"{f' (claim: {claim_number})' if claim_number else ' (claim: pending)'}.\n"
        f"RECOMMENDED ACTION: {disposition} - "
        f"{'review injury advisory before contact. ' if injured else ''}"
        f"Flags: {', '.join(flags)}. Missing: {', '.join(missing) or 'none'}. "
        f"[SYNTHETIC DEMO DATA]"
    )

    record = {
        "intake_id": str(uuid.uuid4()),
        "timestamp": created.isoformat(),
        "workflow_id": WORKFLOW_ID,
        "vertical": VERTICAL,
        "powered_by": "ORCA",
        "client_target": "Birchwood Automotive Group",
        "workflow_status": "demo/pilot",
        "status": "completed" if disposition == "COMPLETED_INTAKE" else "incomplete",
        "demo_seed": True,
        "customer": {"name": name, "phone": phone, "email": None, "address": None},
        "vehicle": {
            "year": year,
            "make": make,
            "model": model,
            "license_plate": None,
            "is_luxury": make in ("BMW",),
            "is_rebuilt": False,
            "is_rebuilt_or_salvage": False,
            "is_drivable": drivable,
        },
        "incident": {
            "damage_type": damage,
            "damage_area": area,
            "is_drivable": drivable,
            "description": narrative,
            "incident_datetime": incident_when,
            "incident_location": location,
            "injuries_state": "reported" if injured else "denied",
            "other_parties": other,
            "police_report_filed": police,
            "photos_available": photos,
            "glass_only": glass_only,
            "body_damage": not glass_only,
        },
        "insurance": {
            "filing_claim": not private_pay,
            "filing_insurance_claim": not private_pay,
            "insurance_provider": insurer,
            "claim_number": claim_number,
            "private_pay": private_pay,
        },
        "location": {
            "preferred_center": f"BIRCHWOOD_COLLISION_LOCATION_{index % 3 + 1}",
            "available_centers": [
                "BIRCHWOOD_COLLISION_LOCATION_1",
                "BIRCHWOOD_COLLISION_LOCATION_2",
                "BIRCHWOOD_COLLISION_LOCATION_3",
            ],
        },
        "flags": flags,
        "missing_information": missing,
        "outcome": {"result": disposition.lower(), "outcome": disposition},
        # Flattened convenience fields (mirrors the live workflow output).
        "caller_name": name,
        "phone": phone,
        "email": None,
        "address": None,
        "vehicle_year": year,
        "vehicle_make": make,
        "vehicle_model": model,
        "license_plate": None,
        "is_drivable": drivable,
        "damage_type": damage,
        "damage_area": area,
        "glass_only": glass_only,
        "body_damage": not glass_only,
        "incident_description": narrative,
        "incident_datetime": incident_when,
        "incident_location": location,
        "injuries_state": "reported" if injured else "denied",
        "other_parties": other,
        "police_report_filed": police,
        "photos_available": photos,
        "filing_insurance_claim": not private_pay,
        "insurance_provider": insurer,
        "claim_number": claim_number,
        "private_pay": private_pay,
        "preferred_collision_center": f"BIRCHWOOD_COLLISION_LOCATION_{index % 3 + 1}",
        "preferred_timing": ["this week", "as soon as possible", "next week"][
            index % 3
        ],
        "human_review_required": injured,
        "callback_needed": callback_needed,
        "plain_summary": "We recorded your collision details and an advisor "
        "will call you back.",
        "shop_summary": shop_summary,
    }

    safety_events = []
    if injured:
        safety_events.append(
            {
                "type": "injury_advisory",
                "flag": "injuries_reported",
                "message": "Caller reported injuries. The assistant advised seeking "
                "medical attention / 9-1-1 before booking. (synthetic demo)",
            }
        )
    if not drivable:
        safety_events.append(
            {
                "type": "rule_triggered",
                "rule_id": "automotive_collision:gate_1_drivability_transfer",
                "flag": "non_drivable_transfer",
            }
        )

    session = OrchestratorSession(
        session_id=f"bwdemo-{run_tag}-{index:02d}",
        call_sid=f"{SEED_CALL_SID_PREFIX}{run_tag}-{index:02d}",
    )
    session.vertical_key = VERTICAL
    session.workflow_id = WORKFLOW_ID
    session.workflow_version = "v1"
    session.is_finalized = True
    session.finalization_reason = "demo_seed"
    session.created_at = created
    session.conversation = _turns(
        name, vehicle_label, damage, drivable, injured, insurer
    )
    session.turn_count = len(session.conversation) // 2
    session.channel_metadata["demo_seed"] = True
    session.channel_metadata["_dashboard_record"] = {
        "created_at": created.isoformat(),
        "ended_at": (created + timedelta(seconds=duration_s)).isoformat(),
        "status": "ended",
    }
    session.channel_metadata["workflow_final_result"] = {
        "final_disposition": disposition,
        "confidence_score": round(_RNG.uniform(0.78, 0.97), 2),
        "summary": narrative,
        "structured_output": {"intake_record": record},
        "safety_events": safety_events,
        "rules_triggered": [],
        "audit_metadata": {"demo_seed": True},
    }
    # Injury records carry no status events: the dashboard derives
    # "escalated" for them, which is exactly the pitch behavior.
    target_status = "new" if injured else _STATUS_PLAN[index % len(_STATUS_PLAN)]
    return session, target_status


def seed_records(count: int = DEFAULT_COUNT, run_tag: str | None = None) -> list[str]:
    """Create `count` synthetic sessions; returns their session ids."""
    repo = get_session_repository()
    tag = run_tag or uuid.uuid4().hex[:6].upper()
    seeded: list[str] = []
    for index in range(count):
        session, target_status = _build_session(index, tag)
        repo.persist_session(session)
        steps = {
            "new": [],
            "contacted": ["contacted"],
            "scheduled": ["contacted", "scheduled"],
            "completed": ["contacted", "scheduled", "completed"],
        }[target_status]
        notes = {
            "contacted": "Reached the customer, going over options.",
            "scheduled": "Estimate booked.",
            "completed": "Vehicle through intake; file closed.",
        }
        for step in steps:
            repo.append_record_status_event(
                session.session_id, step, "demo.seeder", notes[step]
            )
        seeded.append(session.session_id)
    return seeded


def remove_records() -> int:
    """Delete every seeded session (matched by the demo call_sid prefixes)."""
    repo = get_session_repository()
    sessions = repo.list_recent_sessions(limit=1000, vertical_key=VERTICAL)
    prefixes = (SEED_CALL_SID_PREFIX, *LEGACY_SEED_PREFIXES)
    removed = 0
    for session in sessions:
        if (session.call_sid or "").startswith(prefixes):
            repo.delete_session(session.session_id)
            removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remove", action="store_true", help="remove all seeded demo records"
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    args = parser.parse_args(argv)

    if args.remove:
        removed = remove_records()
        print(f"Removed {removed} seeded Birchwood demo records.")
        return 0

    seeded = seed_records(count=args.count)
    print(f"Seeded {len(seeded)} synthetic Birchwood collision records.")
    print(
        "Open /dashboard/birchwood — remove later with: "
        "python -m scripts.seed_birchwood_demo --remove"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
