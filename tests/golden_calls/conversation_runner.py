"""Conversation golden-call runner — Birchwood gated-LLM conversational intake.

Replays a RECORDED conversation (caller utterances + recorded LLM turn outputs)
through the PRODUCTION conversational workflow with the recorded LLM responses
mocked — so it runs deterministically in CI (no external LLM), exactly like the
other golden-call packs. The platform injury-safety overlay is applied each turn
(as the WorkflowEngine does), so the recorded advisory behaviour is covered too.

Case JSON schema (conversation_cases/<vertical>/*.json):
  case_id, description,
  turns: [ {"caller": "<utterance>", "llm": { <BirchwoodTurnOutput fields> }}, ... ]
         (a turn with NO "llm" key is one the workflow resolves WITHOUT calling
          the LLM — e.g. a transfer request),
  expected_disposition (str or list), expected_finalized (bool),
  expected_fields_contain (optional {key: value}),
  expected_flags_contain (optional [..]),
  expected_advisory_spoken (optional bool — injury advisory reached the caller),
  expected_llm_calls (optional int).

The flag BIRCHWOOD_CONVERSATIONAL_INTAKE must be on when running (the test sets it).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

CONVERSATION_CASES_DIR = Path(__file__).parent / "conversation_cases"

REQUIRED_CASE_KEYS = [
    "case_id",
    "description",
    "turns",
    "expected_disposition",
    "expected_finalized",
]


@dataclass
class ConversationGoldenResult:
    case_id: str
    disposition: str | None = None
    finalized: bool = False
    fields: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    assistant_texts: list[str] = field(default_factory=list)
    llm_call_count: int = 0
    error: str | None = None


def load_conversation_cases() -> list[dict]:
    cases: list[dict] = []
    for json_file in sorted(CONVERSATION_CASES_DIR.rglob("*.json")):
        case = json.loads(json_file.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED_CASE_KEYS if k not in case]
        if missing:
            raise RuntimeError(f"{json_file.name} missing keys: {missing}")
        cases.append(case)
    if not cases:
        raise RuntimeError(f"No conversation golden cases in {CONVERSATION_CASES_DIR}")
    return cases


def run_conversation_case(case: dict) -> ConversationGoldenResult:
    """Replay one recorded conversation. Requires BIRCHWOOD_CONVERSATIONAL_INTAKE on."""
    from src.llm.guarded_client import GuardedLLM
    from src.platform.workflows.router import _enforce_turn_safety_overlay
    from src.platform.workflows.schemas import WorkflowContext, WorkflowInput
    from src.verticals.automotive_collision.constants import (
        AUTOMOTIVE_COLLISION_VERTICAL,
        BIRCHWOOD_COLLISION_WORKFLOW_ID,
    )
    from src.verticals.automotive_collision.conversational_intake import (
        BirchwoodTurnOutput,
    )
    from src.verticals.automotive_collision.workflow import (
        BirchwoodCollisionIntakeWorkflow,
    )

    try:
        recorded = [
            BirchwoodTurnOutput(**turn["llm"])
            for turn in case["turns"]
            if "llm" in turn
        ]
        guarded = MagicMock(spec=GuardedLLM)
        guarded.structured_call = AsyncMock(side_effect=recorded)
        wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=guarded)
        ctx = WorkflowContext(
            session_id=case["case_id"],
            vertical=AUTOMOTIVE_COLLISION_VERTICAL,
            workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
            workflow_version="v1",
        )

        async def _drive():
            state = wf.start_session(ctx)
            texts: list[str] = []
            final = None
            for turn in case["turns"]:
                inp = WorkflowInput(user_text=turn["caller"], session_state=state)
                result = await wf.handle_turn(ctx, inp)
                # Mirror the engine: staple the platform safety overlay on top.
                result = _enforce_turn_safety_overlay(ctx, inp, result)
                state = result.updated_state
                texts.append(result.assistant_text)
                final = result
                if result.should_finalize:
                    break
            return final, state, texts

        final, state, texts = asyncio.run(_drive())
        meta = state.get("channel_metadata") or {}
        record = meta.get("automotive_collision") or {}
        fields = (meta.get("scripted_intake") or {}).get("fields") or {}
        return ConversationGoldenResult(
            case_id=case["case_id"],
            disposition=(final.recommended_disposition if final else None),
            finalized=bool(final and final.should_finalize),
            fields=fields,
            flags=list(record.get("flags") or []),
            assistant_texts=texts,
            llm_call_count=guarded.structured_call.call_count,
        )
    except Exception as exc:  # fail closed: an error is a failing result
        return ConversationGoldenResult(
            case_id=case["case_id"], error=f"{type(exc).__name__}: {exc}"
        )
