"""Offline Birchwood conversation simulator (minimal pure-intake flow).

Drives the REAL Twilio webhook handlers (handle_gather / handle_thinking)
with an in-memory backend and TTS disabled — no Twilio, no LLM, no network.
Prints the full conversation and the resulting intake record summary.

The Birchwood workflow is now a MINIMAL pure-intake flow: it only collects
what a collision specialist needs (name, phone, vehicle year/make/model,
damage, drivability, optional MPI claim) and hands off. It never triages,
declines, or transfers; outcomes are only COMPLETED_INTAKE or
INCOMPLETE_CALLBACK_NEEDED. There is no injury question, no narrative
"walk me through what happened" stage, and no readback — so the scenarios
below are purely QUESTION-DRIVEN: the driver answers whatever scripted
stage the engine asks next, keyed by the minimal field name.

Usage:
    python -m scripts.simulate_birchwood_call                 # all scenarios
    python -m scripts.simulate_birchwood_call --scenario needs_tow
    python -m scripts.simulate_birchwood_call --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# Scenarios are QUESTION-DRIVEN: the driver answers whatever scripted stage
# the engine asks next, keyed by the minimal field name. Required fields:
# caller_name, phone, vehicle_year, vehicle_make, vehicle_model, damage_type,
# is_drivable. Optional: filing_insurance_claim, claim_number.
#
# Flags are descriptive data for the specialist (never routing decisions):
#   needs_tow             -> is_drivable is False
#   private_pay           -> filing_insurance_claim is False
#   mpi_claim_in_hand     -> filing_insurance_claim True and a claim number given
#   mpi_claim_open_no_number -> filing_insurance_claim True, no number yet
SCENARIOS: dict[str, dict] = {
    "completed_drivable_claim": {
        "description": "Drivable Toyota, MPI claim in hand — completed intake",
        "answers": {
            "caller_name": "John Smith",
            "phone": "204 555 0101",
            "vehicle_year": "2020",
            "vehicle_make": "Toyota",
            "vehicle_model": "Camry",
            "damage_type": "front bumper and grille",
            "is_drivable": "yes, I can drive it in",
            "filing_insurance_claim": "yes, I've opened an MPI claim",
            "claim_number": "MPI-DEMO-1001",
        },
        "expect_outcome": "COMPLETED_INTAKE",
        "expect_flags": ["mpi_claim_in_hand"],
    },
    "completed_private_pay": {
        "description": "Drivable Mazda, paying privately — completed intake",
        "answers": {
            "caller_name": "Alex Example",
            "phone": "204 555 0123",
            "vehicle_year": "2022",
            "vehicle_make": "Mazda",
            "vehicle_model": "CX-5",
            "damage_type": "rear bumper",
            "is_drivable": "yes, it's safe to drive",
            "filing_insurance_claim": "no, I'll be paying out of pocket",
        },
        "expect_outcome": "COMPLETED_INTAKE",
        "expect_flags": ["private_pay"],
    },
    "needs_tow": {
        "description": "Front end crushed, needs a tow — completed intake, flagged",
        "answers": {
            "caller_name": "Avery Lee",
            "phone": "204 555 0102",
            "vehicle_year": "2018",
            "vehicle_make": "Ford",
            "vehicle_model": "Escape",
            "damage_type": "front end is crushed in",
            "is_drivable": "no, it's not drivable, it needs a tow",
            "filing_insurance_claim": "yes, going through MPI",
            "claim_number": "MPI-DEMO-2002",
        },
        "expect_outcome": "COMPLETED_INTAKE",
        "expect_flags": ["needs_tow", "mpi_claim_in_hand"],
    },
    "claim_open_no_number": {
        "description": "MPI claim opened but no number yet — completed intake, flagged",
        "answers": {
            "caller_name": "Taylor Johnson",
            "phone": "204 555 0106",
            "vehicle_year": "2019",
            "vehicle_make": "Nissan",
            "vehicle_model": "Rogue",
            "damage_type": "passenger side panels",
            "is_drivable": "yes, it drives okay",
            "filing_insurance_claim": "yes, going through MPI",
            "claim_number": "I don't have it yet",
        },
        "expect_outcome": "COMPLETED_INTAKE",
        "expect_flags": ["mpi_claim_open_no_number"],
    },
    "incomplete_missing_year": {
        "description": "Caller can't recall the vehicle year — callback needed",
        "answers": {
            "caller_name": "Casey Nguyen",
            "phone": "204 555 0104",
            # vehicle_year intentionally omitted -> driver answers "I'm not
            # sure", which never parses to a year, so the required field stays
            # missing and the intake finalizes as a callback.
            "vehicle_make": "Honda",
            "vehicle_model": "Civic",
            "damage_type": "driver door",
            "is_drivable": "yes, safe to drive",
            "filing_insurance_claim": "no, paying out of pocket",
        },
        "expect_outcome": "INCOMPLETE_CALLBACK_NEEDED",
        "expect_flags": ["private_pay"],
    },
}

_MAX_TURNS = 25


def _strip_twiml(twiml: str) -> str:
    say = re.findall(r"<Say[^>]*>(.*?)</Say>", twiml, re.DOTALL)
    if say:
        return " ".join(s.strip() for s in say)
    if "<Play>" in twiml:
        return "[audio prompt]"
    return twiml


async def _simulate(
    scenario_name: str,
    *,
    patch_storage: bool = True,
    call_sid: str | None = None,
) -> dict:
    import contextlib

    import src.config  # noqa: F401 — ensure config loads before patching

    stack = contextlib.ExitStack()
    if patch_storage:
        stack.enter_context(patch("src.config.STORAGE_BACKEND", "memory"))
        stack.enter_context(patch("src.config.ENVIRONMENT", "development"))
        stack.enter_context(patch("src.config.APP_ENV", "development"))
        stack.enter_context(patch("src.config.DATABASE_URL", None))
    stack.enter_context(
        patch(
            "src.twilio.routes.text_to_speech_url",
            new=AsyncMock(return_value=None),
        )
    )
    with stack:
        from fastapi import BackgroundTasks

        from src.platform.workflows.router import reset_workflow_route_resolver
        from src.platform.workflows.schemas import ResolvedWorkflowRoute
        from src.storage.factory import reset_storage_backend
        from src.storage.session_repository import (
            get_session_repository,
            reset_session_repository,
        )
        from src.twilio import routes as twilio_routes
        from src.verticals.automotive_collision.constants import (
            AUTOMOTIVE_COLLISION_VERTICAL,
            BIRCHWOOD_COLLISION_WORKFLOW_ID,
        )
        from src.verticals.automotive_collision.workflow import (
            BirchwoodCollisionIntakeWorkflow,
        )

        reset_session_repository()
        reset_storage_backend()
        reset_workflow_route_resolver()
        repo = get_session_repository()

        scenario = SCENARIOS[scenario_name]
        call_sid = call_sid or f"CA-SIM-{scenario_name.upper()}"
        workflow = BirchwoodCollisionIntakeWorkflow()
        intake = workflow.get_scripted_intake_definition()
        first = intake.stages[0]
        session = repo.create_session(
            call_sid=call_sid,
            workflow_route=ResolvedWorkflowRoute(
                vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
                workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
                workflow_version="v1",
            ),
        )
        session.channel_metadata["stage"] = first.stage_id
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": 0,
            "current_stage_id": first.stage_id,
            "fields": {},
            "attempts": {},
            "completed": False,
        }
        repo.persist_session(session)

        transcript: list[tuple[str, str]] = [
            ("assistant", f"{intake.intro_text} {first.prompt}")
        ]
        questions_asked = 1
        stage_by_id = {s.stage_id: s for s in intake.stages}

        async def _send(turn: str) -> str:
            nonlocal questions_asked
            transcript.append(("caller", turn))
            response = await twilio_routes.handle_gather(
                SimpleNamespace(headers={}),
                BackgroundTasks(),
                CallSid=call_sid,
                SpeechResult=turn,
            )
            body = response.body.decode()
            if "/api/v1/voice/thinking" in body:
                task, _ = twilio_routes._pending_turns[call_sid]
                await task
                response = await twilio_routes.handle_thinking(
                    SimpleNamespace(headers={}),
                    BackgroundTasks(),
                    CallSid=call_sid,
                )
                body = response.body.decode()
            transcript.append(("assistant", _strip_twiml(body)))
            if "<Gather" in body:
                questions_asked += 1
            return body

        # Some scenarios may still open with free-text turns; the minimal
        # flow has no narrative stage, so this is normally empty.
        for turn in scenario.get("story", []):
            await _send(turn)

        # Answer whatever scripted stage the engine asks next, until the call
        # finalizes. A field omitted from `answers` (e.g. vehicle_year in the
        # incomplete scenario) is answered with "I'm not sure", which never
        # parses to a valid value — so the required field stays missing.
        answers: dict[str, str] = scenario.get("answers", {})
        for _ in range(_MAX_TURNS):
            stored = repo.load_session_by_call(call_sid)
            if stored is None or stored.is_finalized:
                break
            stage = stage_by_id.get(stored.channel_metadata.get("stage"))
            if stage is None:  # FINAL or unknown — let the engine finalize
                break
            answer = answers.get(stage.field_name)
            if answer is None:
                answer = "I'm not sure"
            await _send(answer)

        stored = repo.load_session_by_call(call_sid)
        final = (stored.channel_metadata.get("workflow_final_result") or {}).get(
            "structured_output", {}
        )
        record = final.get("intake_record", {})
        return {
            "scenario": scenario_name,
            "transcript": transcript,
            "questions_asked": questions_asked,
            "outcome": record.get("recommended_routing"),
            "flags": record.get("flags", []),
            "plain_summary": record.get("plain_summary", ""),
            "shop_summary": record.get("shop_summary", ""),
        }


def simulate(
    scenario_name: str,
    *,
    patch_storage: bool = True,
    call_sid: str | None = None,
) -> dict:
    """Synchronous wrapper used by tests, the CLI, and the seed script."""
    return asyncio.run(
        _simulate(scenario_name, patch_storage=patch_storage, call_sid=call_sid)
    )


def _print_result(result: dict) -> None:
    print("=" * 72)
    print(f"SCENARIO: {result['scenario']}")
    print("=" * 72)
    for role, text in result["transcript"]:
        prefix = "ORCA   >" if role == "assistant" else "CALLER >"
        print(f"{prefix} {text}")
    print("-" * 72)
    print(f"Outcome          : {result['outcome']}")
    print(f"Flags            : {', '.join(result['flags']) or 'none'}")
    print(f"Questions asked  : {result['questions_asked']}")
    print("\nSHOP SUMMARY\n" + result["shop_summary"])
    print("\nCALLER SUMMARY\n" + result["plain_summary"])
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit raw JSON")
    args = parser.parse_args()

    if args.list:
        for name, sc in SCENARIOS.items():
            print(f"{name}: {sc['description']}")
        return 0

    names = [args.scenario] if args.scenario else sorted(SCENARIOS)
    for name in names:
        result = simulate(name)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_result(result)
        expected = SCENARIOS[name].get("expect_outcome")
        if expected and result["outcome"] != expected:
            print(f"!! {name}: expected {expected}, got {result['outcome']}")
            return 1
        for flag in SCENARIOS[name].get("expect_flags", []):
            if flag not in result["flags"]:
                print(f"!! {name}: expected flag {flag} missing")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
