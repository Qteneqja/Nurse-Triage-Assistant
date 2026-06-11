"""Offline Birchwood conversation simulator (PR 2).

Drives the REAL Twilio webhook handlers (handle_gather / handle_thinking)
with an in-memory backend and TTS disabled — no Twilio, no LLM, no network.
Prints the full conversation and the resulting intake record summary.

Usage:
    python -m scripts.simulate_birchwood_call                 # all scenarios
    python -m scripts.simulate_birchwood_call --scenario injury_branch
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

# Scenarios are QUESTION-DRIVEN: the driver answers whatever stage the
# engine actually asks next (gap-fill skips questions the story already
# answered, so a fixed turn list would drift out of sync).
SCENARIOS: dict[str, dict] = {
    "rich_story_completed": {
        "description": "Detailed story — extraction prefills, minimal gap-fill",
        "story": [
            "I got rear-ended yesterday evening at Pembina and Stafford. "
            "It's my 2021 Toyota Corolla - the rear bumper and trunk are "
            "smashed up but it still drives fine. The other driver and I "
            "exchanged information, the police came and took a report, and "
            "I took some photos. I'm going through MPI, the claim number "
            "is MPI-4452-21, and nobody was hurt.",
            "that's everything",
        ],
        "answers": {
            "rebuilt_salvage_status": "no never",
            "caller_name": "Pat Tester",
            "phone": "204 555 0101",
            "confirmation_ack": "yes that's right",
        },
        "expect_outcome": "COMPLETED_INTAKE",
        "expect_flags": ["injuries_denied"],
    },
    "injury_branch": {
        "description": "Injury mentioned mid-story — advisory + flagged record",
        "story": [
            "Someone ran a stop sign and hit my driver door, and my neck "
            "has been sore since it happened.",
            "that's everything",
        ],
        "answers": {
            "is_drivable": "yes it still drives",
            "damage_type": "driver door and front fender",
            "vehicle_year": "2019",
            "vehicle_make": "Honda",
            "vehicle_model": "Civic",
            "rebuilt_salvage_status": "no never",
            "incident_datetime": "this morning",
            "incident_location": "McPhillips and Leila",
            "filing_insurance_claim": "going through MPI",
            "claim_number": "I don't have it yet",
            "caller_name": "Sam Caller",
            "phone": "204 555 0199",
            "confirmation_ack": "yes",
        },
        "expect_flags": ["injuries_reported"],
    },
    "sparse_story_gap_fill": {
        "description": "One-line story — targeted gap-fill asks each required field",
        "story": [
            "Someone bumped into my car.",
            "that's everything",
        ],
        "answers": {
            "injuries_state": "no nobody was hurt",
            "is_drivable": "yes it's drivable",
            "damage_type": "rear bumper",
            "vehicle_year": "2022",
            "vehicle_make": "Mazda",
            "vehicle_model": "CX-5",
            "rebuilt_salvage_status": "no",
            "incident_datetime": "last Friday",
            "incident_location": "Osborne Village",
            "filing_insurance_claim": "paying out of pocket myself",
            "caller_name": "Alex Example",
            "phone": "204 555 0123",
            "confirmation_ack": "yes that's correct",
        },
        "expect_outcome": "COMPLETED_INTAKE",
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

        for turn in scenario["story"]:
            await _send(turn)

        # Answer whatever the engine asks next, until the call finalizes.
        answers: dict[str, str] = scenario.get("answers", {})
        for _ in range(_MAX_TURNS):
            stored = repo.load_session_by_call(call_sid)
            if stored is None or stored.is_finalized:
                break
            stage = stage_by_id.get(stored.channel_metadata.get("stage"))
            if stage is None:  # DYNAMIC or unknown — let the engine finalize
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
            "injuries_state": record.get("injuries_state"),
            "plain_summary": record.get("plain_summary", ""),
            "shop_summary": record.get("shop_summary", ""),
            "narrative_extraction": stored.channel_metadata.get(
                "narrative_extraction", {}
            ),
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
    print(f"Injuries state   : {result['injuries_state']}")
    print(f"Questions asked  : {result['questions_asked']}")
    prefilled = result["narrative_extraction"].get("prefilled", [])
    print(f"Prefilled fields : {', '.join(e['field'] for e in prefilled) or 'none'}")
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
