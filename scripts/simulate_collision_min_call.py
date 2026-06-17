"""Offline simulator for the minimal Birchwood collision-intake workflow.

Drives birchwood_collision_intake_min_v1 through the REAL WorkflowEngine (so the
shared platform safety overlay - the reactive emergency reflex - is exercised),
with no Twilio, no LLM, no network. Field capture is provided as already-parsed
scripted-intake fields (the scripted-intake transport machine is tested in the
routes/gather tests); this runner validates the workflow logic + the shared
baseline end-to-end.

Usage:
    python -m scripts.simulate_collision_min_call            # all scenarios
    python -m scripts.simulate_collision_min_call --scenario drivable_with_claim
    python -m scripts.simulate_collision_min_call --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

# Each scenario: scripted-intake fields the specialist needs, an optional free
# caller utterance (dynamic_text), and expectations. PROVISIONAL demo data.
SCENARIOS: dict[str, dict] = {
    "drivable_with_claim": {
        "description": "Drivable + MPI claim in hand -> full intake -> handoff",
        "fields": {
            "caller_name": "John Smith",
            "callback_number": "204 555 0141",
            "vehicle_year": "2020",
            "vehicle_make": "Toyota",
            "vehicle_model": "Camry",
            "damage_description": "front bumper and grille",
            "drivable_status": "yes, it's safe to drive",
            "mpi_claim_opened": True,
            "mpi_claim_number": "CLM-DEMO-1001",
        },
        "dynamic_text": "that's everything",
        "expect_outcome": "READY_FOR_SPECIALIST",
        "expect_flags": ["drivable", "mpi_claim_in_hand"],
    },
    "not_drivable_tow": {
        "description": "Not drivable -> location captured, tow flagged -> handoff",
        "fields": {
            "caller_name": "Avery Lee",
            "callback_number": "204 555 0142",
            "vehicle_year": "2019",
            "vehicle_make": "Honda",
            "vehicle_model": "CR-V",
            "damage_description": "front end is crushed",
            "drivable_status": "no, it needs a tow",
            "vehicle_location": "Portage and Main, Winnipeg",
            "mpi_claim_opened": True,
            "mpi_claim_number": "CLM-DEMO-1002",
        },
        "dynamic_text": "that's everything",
        "expect_outcome": "READY_FOR_SPECIALIST",
        "expect_flags": ["needs_tow"],
    },
    "no_claim_yet": {
        "description": "No MPI claim yet -> intake captured, no-claim flagged -> handoff",
        "fields": {
            "caller_name": "Morgan Patel",
            "callback_number": "204 555 0143",
            "vehicle_year": "2021",
            "vehicle_make": "Mazda",
            "vehicle_model": "CX-5",
            "damage_description": "rear bumper and trunk",
            "drivable_status": "yes, it drives fine",
            "mpi_claim_opened": False,
        },
        "dynamic_text": "that's everything",
        "expect_outcome": "READY_FOR_SPECIALIST",
        "expect_flags": ["no_mpi_claim"],
    },
    "asks_for_estimate": {
        "description": "Caller asks cost/coverage -> agent defers, completes intake -> handoff",
        "fields": {
            "caller_name": "Casey Nguyen",
            "callback_number": "204 555 0144",
            "vehicle_year": "2022",
            "vehicle_make": "Subaru",
            "vehicle_model": "Outback",
            "damage_description": "driver door dented",
            "drivable_status": "yes",
            "mpi_claim_opened": True,
            "mpi_claim_number": "CLM-DEMO-1004",
        },
        "dynamic_text": "how much will this cost, and will MPI cover it?",
        "expect_outcome": "READY_FOR_SPECIALIST",
        "expect_flags": ["estimate_request_deflected"],
    },
    # Shared baseline (NOT a collision feature): a spontaneous injury mention is
    # handled by the platform overlay - advisory + flag + human review. The
    # workflow never asks about injuries.
    "emergency_reflex": {
        "description": "Caller spontaneously says they're hurt -> shared emergency reflex",
        "fields": {
            "caller_name": "Sam Rivera",
            "callback_number": "204 555 0145",
            "vehicle_year": "2020",
            "vehicle_make": "Ford",
            "vehicle_model": "Escape",
            "damage_description": "side impact",
            "drivable_status": "no, needs a tow",
            "vehicle_location": "Main Street",
            "mpi_claim_opened": False,
        },
        "dynamic_text": "honestly my neck really hurts and I feel dizzy",
        "expect_flags": ["injuries_reported"],
        "expect_advisory": True,
    },
}


async def _simulate(scenario_name: str) -> dict:
    from src.platform.workflows.registry import (
        ensure_default_workflows_registered,
        reset_workflow_registry,
    )
    from src.platform.workflows.router import get_workflow_engine
    from src.platform.workflows.schemas import WorkflowContext, WorkflowInput
    from src.orchestrator.schemas import ConversationTurn, OrchestratorSession
    from src.verticals.collision_intake_min.constants import (
        COLLISION_MIN_VERTICAL,
        COLLISION_MIN_WORKFLOW_ID,
        COLLISION_MIN_WORKFLOW_VERSION,
    )

    reset_workflow_registry()
    ensure_default_workflows_registered()

    scenario = SCENARIOS[scenario_name]
    session = OrchestratorSession(
        session_id=f"SIM-{scenario_name}",
        call_sid=f"CA-MIN-{scenario_name.upper()}",
    )
    session.vertical_key = COLLISION_MIN_VERTICAL
    session.workflow_id = COLLISION_MIN_WORKFLOW_ID
    session.channel_metadata["scripted_intake"] = {
        "workflow_id": COLLISION_MIN_WORKFLOW_ID,
        "fields": dict(scenario["fields"]),
        "completed": True,
    }
    dynamic_text = scenario.get("dynamic_text", "")
    if dynamic_text:
        session.conversation.append(ConversationTurn(role="caller", text=dynamic_text))

    context = WorkflowContext(
        session_id=session.session_id,
        vertical=COLLISION_MIN_VERTICAL,
        workflow_id=COLLISION_MIN_WORKFLOW_ID,
        workflow_version=COLLISION_MIN_WORKFLOW_VERSION,
        call_sid=session.call_sid,
    )
    workflow_input = WorkflowInput(
        user_text=dynamic_text,
        session_state=session.model_dump(mode="json"),
        metadata={"channel": "simulation"},
    )

    result = await get_workflow_engine().handle_turn(context, workflow_input)
    record = (
        (result.updated_state.get("channel_metadata") or {})
        .get("workflow_final_result", {})
        .get("structured_output", {})
        .get("intake_record", {})
    )
    return {
        "scenario": scenario_name,
        "assistant_text": result.assistant_text,
        "outcome": record.get("recommended_routing"),
        "handoff_mode": record.get("handoff_mode"),
        "flags": list(record.get("flags") or []),
        "missing_information": list(record.get("missing_information") or []),
        "human_review_required": record.get("human_review_required"),
        "handoff_summary": record.get("handoff_summary", ""),
        "record": record,
    }


def simulate(scenario_name: str) -> dict:
    """Synchronous wrapper used by tests and the CLI."""
    return asyncio.run(_simulate(scenario_name))


def _print(result: dict) -> None:
    print("=" * 72)
    print(f"SCENARIO: {result['scenario']}")
    print("=" * 72)
    print(f"Outcome     : {result['outcome']} ({result['handoff_mode']})")
    print(f"Flags       : {', '.join(result['flags']) or 'none'}")
    print(f"Missing     : {', '.join(result['missing_information']) or 'none'}")
    print(f"Human review: {result['human_review_required']}")
    print(f"\nORCA says   : {result['assistant_text']}")
    print(f"\nHANDOFF     : {result['handoff_summary']}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.list:
        for name, sc in SCENARIOS.items():
            print(f"{name}: {sc['description']}")
        return 0

    names = [args.scenario] if args.scenario else sorted(SCENARIOS)
    rc = 0
    for name in names:
        result = simulate(name)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print(result)
        expected = SCENARIOS[name].get("expect_outcome")
        if expected and result["outcome"] != expected:
            print(f"!! {name}: expected {expected}, got {result['outcome']}")
            rc = 1
        for flag in SCENARIOS[name].get("expect_flags", []):
            if flag not in result["flags"]:
                print(f"!! {name}: expected flag {flag} missing")
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
