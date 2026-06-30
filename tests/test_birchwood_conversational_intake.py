"""Tests for the Birchwood gated-LLM conversational intake (premium tier).

The conversational tier (BIRCHWOOD_CONVERSATIONAL_INTAKE) runs an LLM-driven
fluid conversation instead of the scripted Q&A. These tests verify the
invariants that make it SAFE and ORCA-compliant:

* the disposition stays DETERMINISTIC (classify_collision_intake), the LLM never
  decides it;
* every caller-facing utterance passes the safety gate (proved by injecting a
  role-claim and seeing it sanitized);
* any LLM failure fails closed to a human callback;
* a transfer request / turn cap hands off;
* and with the flag OFF the scripted flow is completely unchanged.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import src.config as config
from src.llm.client import LLMCallError
from src.llm.guarded_client import GuardedLLM
from src.platform.workflows.schemas import WorkflowContext, WorkflowInput
from src.verticals.automotive_collision import conversational_intake as ci
from src.verticals.automotive_collision.constants import (
    AUTOMOTIVE_COLLISION_VERTICAL,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.orchestrator.schemas import ConversationTurn, OrchestratorSession
from src.verticals.automotive_collision.conversational_intake import BirchwoodTurnOutput
from src.verticals.automotive_collision.workflow import BirchwoodCollisionIntakeWorkflow


def _session_after_question(question: str, caller_reply: str) -> OrchestratorSession:
    s = OrchestratorSession(session_id="cap-test")
    s.conversation.append(ConversationTurn(role="assistant", text=question))
    s.conversation.append(ConversationTurn(role="caller", text=caller_reply))
    return s


def _session_fields(session: OrchestratorSession) -> dict:
    return session.channel_metadata.get("scripted_intake", {}).get("fields", {})


def _ctx() -> WorkflowContext:
    return WorkflowContext(
        session_id="conv-test",
        vertical=AUTOMOTIVE_COLLISION_VERTICAL,
        workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
        workflow_version="v1",
    )


def _mock_guarded(outputs: list[BirchwoodTurnOutput]) -> MagicMock:
    guarded = MagicMock(spec=GuardedLLM)
    guarded.structured_call = AsyncMock(side_effect=outputs)
    return guarded


def _run(wf: BirchwoodCollisionIntakeWorkflow, ctx, utterances: list[str]):
    """Drive turns until finalize; return (results, final_state)."""

    async def _drive():
        state = wf.start_session(ctx)
        results = []
        for utterance in utterances:
            result = await wf.handle_turn(
                ctx, WorkflowInput(user_text=utterance, session_state=state)
            )
            state = result.updated_state
            results.append(result)
            if result.should_finalize:
                break
        return results, state

    return asyncio.run(_drive())


def _fields(state: dict) -> dict:
    return (state["channel_metadata"].get("scripted_intake") or {}).get("fields") or {}


# ---------------------------------------------------------------------------
# Flag gating: off = scripted (unchanged), on = greeting-only -> dynamic loop
# ---------------------------------------------------------------------------


def test_flag_off_keeps_scripted_stages(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", False)
    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    assert intake.stages, "scripted flow must keep its stages when the flag is off"
    assert any(s.field_name == "vehicle_make" for s in intake.stages)


def test_flag_on_returns_greeting_only_intake(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    assert intake.stages == []  # no scripted stages -> session starts in DYNAMIC
    assert "Aurora" in intake.intro_text
    assert "automated assistant" in intake.intro_text  # disclosure preserved
    # The conversational greeting OPENS THE FLOOR so the caller knows to start.
    assert "tell me what happened" in intake.intro_text.lower()


def test_looks_like_name_guards():
    assert ci._looks_like_name("Jane Doe")
    assert ci._looks_like_name("O'Brien")
    assert not ci._looks_like_name("yes")  # filler, not a name
    assert not ci._looks_like_name("no thanks")  # all filler words
    assert not ci._looks_like_name("2018")  # has digits
    assert not ci._looks_like_name("is it going to cost a lot?")  # question / too long


def test_safety_net_captures_name_when_llm_misses_it():
    s = _session_after_question("And can I get your full name?", "Jane Doe")
    out = BirchwoodTurnOutput(response_to_caller="Sorry, your name?", caller_name=None)
    ci.capture_missing_identity_fields(s, "Jane Doe", out)
    assert _session_fields(s)["caller_name"] == "Jane Doe"


def test_safety_net_ignores_filler_reply_as_name():
    s = _session_after_question("What's your name?", "yes")
    out = BirchwoodTurnOutput(response_to_caller="...", caller_name=None)
    ci.capture_missing_identity_fields(s, "yes", out)
    assert "caller_name" not in _session_fields(s)


def test_safety_net_captures_phone_from_digits():
    s = _session_after_question("Best callback number?", "431 555 0199")
    out = BirchwoodTurnOutput(response_to_caller="...", phone=None)
    ci.capture_missing_identity_fields(s, "431 555 0199", out)
    assert _session_fields(s)["phone"] == "431 555 0199"


def test_safety_net_ignores_non_phone_reply():
    s = _session_after_question("Best callback number?", "I'm not sure right now")
    out = BirchwoodTurnOutput(response_to_caller="...", phone=None)
    ci.capture_missing_identity_fields(s, "I'm not sure right now", out)
    assert "phone" not in _session_fields(s)


def test_safety_net_only_fires_for_the_asked_field():
    # Last question was about the vehicle, not the name -> don't capture a name.
    s = _session_after_question("What year is the vehicle?", "Jane Doe")
    out = BirchwoodTurnOutput(response_to_caller="...", caller_name=None)
    ci.capture_missing_identity_fields(s, "Jane Doe", out)
    assert "caller_name" not in _session_fields(s)


def test_safety_net_recovers_name_in_full_turn_flow(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    outputs = [
        BirchwoodTurnOutput(response_to_caller="Sorry to hear that - your full name?"),
        # The model asks again but leaves caller_name empty (the live failure).
        BirchwoodTurnOutput(response_to_caller="And your name?", caller_name=None),
    ]
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=_mock_guarded(outputs))
    _, state = _run(wf, _ctx(), ["I had an accident", "Jane Doe"])
    fields = state["channel_metadata"]["scripted_intake"]["fields"]
    assert fields.get("caller_name") == "Jane Doe"  # captured despite the LLM miss


def test_turn_output_coerces_numeric_and_boolean_fields():
    # LLMs routinely return year/phone/claim as JSON numbers and yes/no as
    # booleans; the schema must coerce them rather than reject the turn.
    out = BirchwoodTurnOutput(
        response_to_caller="okay",
        vehicle_year=2019,
        phone=2045550123,
        claim_number=774122,
        is_drivable=True,
        filing_insurance_claim=False,
    )
    assert out.vehicle_year == "2019"
    assert out.phone == "2045550123"
    assert out.claim_number == "774122"
    assert out.is_drivable == "yes"
    assert out.filing_insurance_claim == "no"


# ---------------------------------------------------------------------------
# The fluid conversation: accumulate fields -> deterministic finalize
# ---------------------------------------------------------------------------


def test_conversation_accumulates_fields_then_finalizes_deterministically(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    guarded = _mock_guarded(
        [
            BirchwoodTurnOutput(
                response_to_caller="Sorry to hear that — your full name?"
            ),
            BirchwoodTurnOutput(
                response_to_caller="Thanks. Best callback number?",
                caller_name="Jane Doe",
            ),
            BirchwoodTurnOutput(
                response_to_caller="Year, make and model?", phone="204 555 0123"
            ),
            BirchwoodTurnOutput(
                response_to_caller="What's the damage, and can you drive it in?",
                vehicle_year="2019",
                vehicle_make="chevy",  # normalized on merge
                vehicle_model="Civic",
            ),
            BirchwoodTurnOutput(
                response_to_caller="Let me get you booked in.",
                damage_type="rear bumper",
                is_drivable="yes",
            ),
            BirchwoodTurnOutput(response_to_caller="All set!", ready_to_finalize=True),
        ]
    )
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=guarded)
    results, state = _run(
        wf,
        _ctx(),
        [
            "rear-ended",
            "Jane Doe",
            "204 555 0123",
            "2019 chevy civic",
            "bumper, drives",
            "yep",
        ],
    )

    # First five turns kept the conversation open; the sixth finalized.
    assert [r.should_continue for r in results] == [True, True, True, True, True, False]
    final = results[-1]
    assert final.should_finalize is True
    assert final.recommended_disposition == "COMPLETED_INTAKE"  # deterministic
    fields = _fields(state)
    assert fields["caller_name"] == "Jane Doe"
    assert (
        fields["vehicle_make"] == "Chevrolet"
    )  # make canonicalised exactly like scripted
    assert fields["is_drivable"] == "yes"


def test_ready_to_finalize_ignored_while_required_field_missing(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    # LLM claims it's ready, but no required fields captured -> keep talking.
    guarded = _mock_guarded(
        [BirchwoodTurnOutput(response_to_caller="All done?", ready_to_finalize=True)]
    )
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=guarded)
    results, _ = _run(wf, _ctx(), ["hi"])
    assert results[0].should_continue is True
    assert results[0].should_finalize is False


# ---------------------------------------------------------------------------
# Safety: gate runs on every utterance, fail-closed, transfer, turn cap
# ---------------------------------------------------------------------------


def test_role_claim_in_llm_output_is_sanitized_by_the_gate(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    # Real GuardedLLM + mock raw client -> the REAL gate runs on the output.
    mock_client = MagicMock()
    mock_client.call = AsyncMock(
        return_value=BirchwoodTurnOutput(
            response_to_caller="I am a nurse and I can diagnose your injuries."
        )
    )
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=GuardedLLM(_client=mock_client))
    results, _ = _run(wf, _ctx(), ["my arm hurts"])
    spoken = results[0].assistant_text
    # The role/credential claim must NOT reach the caller verbatim.
    assert "I am a nurse" not in spoken
    assert "diagnose" not in spoken


def test_injury_reflex_applies_to_a_conversational_turn(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    from src.platform.workflows.router import _enforce_turn_safety_overlay
    from src.safety.injury_detection import INJURY_SAFETY_ADVISORY

    guarded = _mock_guarded(
        [BirchwoodTurnOutput(response_to_caller="Okay, and what's the make?")]
    )
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=guarded)
    ctx = _ctx()
    state = wf.start_session(ctx)
    inp = WorkflowInput(user_text="my neck really hurts", session_state=state)
    result = asyncio.run(wf.handle_turn(ctx, inp))
    # The engine staples the injury advisory on top of the workflow's output —
    # independent of whether the turn was scripted or LLM-driven.
    guarded_result = _enforce_turn_safety_overlay(ctx, inp, result)
    assert INJURY_SAFETY_ADVISORY in guarded_result.assistant_text


def test_single_turn_failure_recovers_in_call(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    guarded = MagicMock(spec=GuardedLLM)
    guarded.structured_call = AsyncMock(side_effect=LLMCallError("boom"))
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=guarded)
    results, _ = _run(wf, _ctx(), ["hi there"])
    # A single failure must NOT end the call — it recovers and keeps going.
    assert results[0].should_finalize is False
    assert results[0].should_continue is True
    assert "say that again" in results[0].assistant_text.lower()


def test_unexpected_exception_also_recovers_not_crashes(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    guarded = MagicMock(spec=GuardedLLM)
    # A non-LLMCallError (API error / timeout / bad input) must also recover,
    # never propagate to the transport and drop the call.
    guarded.structured_call = AsyncMock(side_effect=ValueError("malformed"))
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=guarded)
    results, _ = _run(wf, _ctx(), ["?!#@"])
    assert results[0].should_finalize is False
    assert results[0].should_continue is True


def test_repeated_failures_hand_off_to_callback(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    guarded = MagicMock(spec=GuardedLLM)
    guarded.structured_call = AsyncMock(side_effect=LLMCallError("boom"))
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=guarded)
    # MAX_ERRORS consecutive failures -> graceful hand off (never an abrupt hangup).
    results, state = _run(wf, _ctx(), ["a"] * ci.MAX_ERRORS)
    assert results[-1].should_finalize is True
    assert results[-1].recommended_disposition == "INCOMPLETE_CALLBACK_NEEDED"
    assert state["finalization_reason"].endswith("llm_error")


def test_transfer_request_finalizes_without_calling_the_llm(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    guarded = _mock_guarded([])  # must never be called
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=guarded)
    results, state = _run(wf, _ctx(), ["can I talk to a real person"])
    assert results[0].should_finalize is True
    guarded.structured_call.assert_not_called()
    assert state["finalization_reason"].endswith("transfer_request")


def test_caller_wants_human_flag_finalizes(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    guarded = _mock_guarded(
        [
            BirchwoodTurnOutput(
                response_to_caller="Let me get someone.", caller_wants_human=True
            )
        ]
    )
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=guarded)
    results, _ = _run(wf, _ctx(), ["this is complicated"])
    assert results[0].should_finalize is True


def test_turn_cap_hands_off(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    # Always "keep going" — the cap must stop the loop.
    guarded = MagicMock(spec=GuardedLLM)
    guarded.structured_call = AsyncMock(
        return_value=BirchwoodTurnOutput(response_to_caller="and...?")
    )
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=guarded)
    results, state = _run(wf, _ctx(), ["um"] * (ci.MAX_TURNS + 2))
    assert results[-1].should_finalize is True
    assert state["finalization_reason"].endswith("turn_cap")


# ---------------------------------------------------------------------------
# Field merge + missing-required helpers
# ---------------------------------------------------------------------------


def test_missing_required_fields_helper():
    assert set(ci.missing_required_fields({})) == set(ci.REQUIRED_FIELDS)
    full = {f: "x" for f in ci.REQUIRED_FIELDS}
    assert ci.missing_required_fields(full) == []


def test_merge_only_writes_nonempty_and_normalizes_make():
    from src.orchestrator.schemas import OrchestratorSession

    session = OrchestratorSession(session_id="s", call_sid="c")
    session.channel_metadata["scripted_intake"] = {"fields": {}}
    ci.merge_extracted_fields(
        session,
        BirchwoodTurnOutput(
            response_to_caller="x", vehicle_make="vw", caller_name=None, phone="  "
        ),
    )
    fields = session.channel_metadata["scripted_intake"]["fields"]
    assert fields["vehicle_make"] == "Volkswagen"  # canonicalised
    assert "caller_name" not in fields  # None not written
    assert "phone" not in fields  # whitespace-only not written
