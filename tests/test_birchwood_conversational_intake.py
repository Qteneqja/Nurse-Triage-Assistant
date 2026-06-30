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
    # The greeting now asks for the NAME first (damage comes later in the flow).
    assert "full name" in intake.intro_text.lower()
    assert "what happened" not in intake.intro_text.lower()


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


def test_safety_net_captures_spoken_phone_number():
    # Phone numbers are routinely transcribed as words, not digits.
    spoken = "four three one five five five oh one nine nine"
    s = _session_after_question("And the best callback number to reach you?", spoken)
    out = BirchwoodTurnOutput(response_to_caller="...", phone=None)
    ci.capture_missing_identity_fields(s, spoken, out)
    assert _session_fields(s)["phone"] == "4315550199"


def test_safety_net_does_not_log_claim_number_as_phone():
    # The MPI claim-number question also contains "number" — must NOT be captured.
    s = _session_after_question(
        "Do you have your MPI claim number?", "one two three four five six seven eight"
    )
    out = BirchwoodTurnOutput(response_to_caller="...", phone=None)
    ci.capture_missing_identity_fields(
        s, "one two three four five six seven eight", out
    )
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


def test_required_fields_match_the_new_goal():
    assert ci.REQUIRED_FIELDS == (
        "caller_name",
        "phone",
        "email",
        "vehicle_year",
        "vehicle_make",
        "vehicle_model",
        "damage_type",
        "license_plate",
        "preferred_collision_center",
        "claim_type",
    )


def test_infer_claim_type():
    assert ci.infer_claim_type({"damage_type": "cracked windshield"}) == "Glass Claim"
    assert (
        ci.infer_claim_type({"damage_type": "rear bumper", "is_drivable": "no"})
        == "Tow In Request"
    )
    assert (
        ci.infer_claim_type({"damage_type": "front bumper cracked"})
        == "Physical Damage"
    )
    assert ci.infer_claim_type({}) is None  # nothing to go on yet


def test_claim_type_inferred_when_damage_captured():
    s = _session_after_question("What's the damage?", "cracked windshield")
    out = BirchwoodTurnOutput(
        response_to_caller="...", damage_type="cracked windshield"
    )
    ci.merge_extracted_fields(s, out)
    ci.capture_missing_identity_fields(s, "cracked windshield", out)
    assert _session_fields(s)["claim_type"] == "Glass Claim"


def test_prompt_carries_locations_and_knowledge():
    assert "Headingley" in ci.SYSTEM_PROMPT
    assert "Regent" in ci.SYSTEM_PROMPT
    assert "I-CAR" in ci.SYSTEM_PROMPT


def test_claim_number_required_only_when_a_claim_was_filed():
    base = {
        "caller_name": "Jane",
        "phone": "2045550100",
        "email": "j@x.co",
        "vehicle_year": "2021",
        "vehicle_make": "Honda",
        "vehicle_model": "Civic",
        "damage_type": "rear bumper",
        "license_plate": "ABC123",
        "preferred_collision_center": "Regent",
        "claim_type": "Physical Damage",
    }
    assert ci.missing_required_fields(base) == []  # no claim -> claim_number optional
    with_claim = {**base, "filing_insurance_claim": "yes"}
    assert "claim_number" in ci.missing_required_fields(with_claim)
    assert ci.missing_required_fields({**with_claim, "claim_number": "7788"}) == []


def test_reconstruct_spelled_word():
    assert ci.reconstruct_spelled_word("Q L I R I M") == "Qlirim"
    assert ci.reconstruct_spelled_word("Q L I R I M Tene") == "Qlirim Tene"
    assert ci.reconstruct_spelled_word("Jane Doe") == ""  # not spelled out


def test_reconstruct_email_keeps_single_letters_literal():
    assert ci.reconstruct_email("q l i r i m at gmail dot com") == "qlirim@gmail.com"
    assert ci.reconstruct_email("qlirim at gmail dot com") == "qlirim@gmail.com"
    assert ci.reconstruct_email("john at example dot co dot uk") == "john@example.co.uk"
    # "o" must stay a letter, never become a zero:
    assert ci.reconstruct_email("j o e at aol dot com") == "joe@aol.com"
    assert ci.reconstruct_email("just my name") == ""  # not an email


def test_capture_email_from_spelled_reply():
    spoken = "q l i r i m at gmail dot com"
    s = _session_after_question("Could you spell out your email for me?", spoken)
    out = BirchwoodTurnOutput(response_to_caller="...", email=None)
    ci.capture_missing_identity_fields(s, spoken, out)
    assert _session_fields(s)["email"] == "qlirim@gmail.com"


def test_capture_licence_plate():
    s = _session_after_question("And the licence plate?", "B C D 1 2 3")
    out = BirchwoodTurnOutput(response_to_caller="...", license_plate=None)
    ci.capture_missing_identity_fields(s, "B C D 1 2 3", out)
    assert _session_fields(s)["license_plate"] == "BCD123"


def test_capture_spelled_name():
    s = _session_after_question("Could you spell your name for me?", "Q L I R I M")
    out = BirchwoodTurnOutput(response_to_caller="...", caller_name=None)
    ci.capture_missing_identity_fields(s, "Q L I R I M", out)
    assert _session_fields(s)["caller_name"] == "Qlirim"


def test_merge_writes_new_fields():
    s = OrchestratorSession(session_id="merge")
    out = BirchwoodTurnOutput(
        response_to_caller="ok",
        email="a@b.co",
        license_plate="ABC123",
        preferred_location="Birchwood Regent",
    )
    ci.merge_extracted_fields(s, out)
    fields = _session_fields(s)
    assert fields["email"] == "a@b.co"
    assert fields["license_plate"] == "ABC123"
    assert fields["preferred_collision_center"] == "Birchwood Regent"


def test_extract_damage_bi_severe_rear_end():
    bi = ci.extract_damage_bi(
        "Rear end got smashed in, airbags went off, can't drive it",
        {"is_drivable": "no"},
    )
    assert bi["severity"] == "severe"
    assert "rear" in bi["impact_areas"]
    assert bi["collision_type"] == "rear_end"
    assert bi["airbags_deployed"] is True
    assert bi["drivable"] == "no"


def test_extract_damage_bi_minor_glass():
    bi = ci.extract_damage_bi("just a small chip in the windshield")
    assert bi["severity"] == "minor"
    assert bi["glass_involved"] is True


def test_run_turn_uses_non_json_mode_for_latency():
    guarded = MagicMock(spec=GuardedLLM)
    guarded.structured_call = AsyncMock(
        return_value=BirchwoodTurnOutput(response_to_caller="hi")
    )
    asyncio.run(ci.run_turn(guarded, OrchestratorSession(session_id="x"), "hello"))
    assert guarded.structured_call.call_args.kwargs["json_mode"] is False


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
                response_to_caller="And the best email?", phone="204 555 0123"
            ),
            BirchwoodTurnOutput(
                response_to_caller="What's the vehicle - year, make, model?",
                email="jane@example.com",
            ),
            BirchwoodTurnOutput(
                response_to_caller="What's the damage?",
                vehicle_year="2019",
                vehicle_make="Honda",
                vehicle_model="Civic",
            ),
            BirchwoodTurnOutput(
                response_to_caller="What's the licence plate?",
                damage_type="rear bumper",  # claim_type inferred -> Physical Damage
            ),
            BirchwoodTurnOutput(
                response_to_caller="Which Birchwood location works best?",
                license_plate="ABC123",
            ),
            BirchwoodTurnOutput(
                response_to_caller="Let me get you booked in.",
                preferred_location="Birchwood Regent",
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
            "jane at example dot com",
            "2019 Honda Civic",
            "rear bumper",
            "ABC123",
            "the Regent one",
            "yep",
        ],
    )

    # The conversation stays open until every required field is captured, then finalizes.
    assert results[-1].should_finalize is True
    assert results[-1].recommended_disposition == "COMPLETED_INTAKE"  # deterministic
    fields = _fields(state)
    assert fields["caller_name"] == "Jane Doe"
    assert fields["email"] == "jane@example.com"
    assert fields["vehicle_make"] == "Honda"
    assert fields["license_plate"] == "ABC123"
    assert fields["preferred_collision_center"] == "Birchwood Regent"
    assert fields["claim_type"] == "Physical Damage"  # inferred from the damage


def test_wrap_up_offers_questions_before_ending(monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    # Turn 1: every required field arrives AND the model declares it's ready — but
    # since data just completed this turn, we must NOT end yet (so the
    # advisor-callback + "any questions?" offer happens). Turn 2: caller is done.
    complete = BirchwoodTurnOutput(
        response_to_caller="You're all set - an advisor will call you back. Any questions?",
        caller_name="Jane Doe",
        phone="2045550100",
        email="j@x.co",
        vehicle_year="2019",
        vehicle_make="Honda",
        vehicle_model="Civic",
        damage_type="rear bumper",
        license_plate="ABC123",
        preferred_location="Regent",
        ready_to_finalize=True,
    )
    done = BirchwoodTurnOutput(response_to_caller="Great!", ready_to_finalize=True)
    wf = BirchwoodCollisionIntakeWorkflow(guarded_llm=_mock_guarded([complete, done]))
    results, _ = _run(wf, _ctx(), ["here's everything", "no, that's all"])

    # First complete turn: data done + model "ready", but the call stays open so the
    # caller is offered questions first.
    assert results[0].should_finalize is False
    assert results[0].should_continue is True
    # Next turn: now it finalizes, speaking the short conversational outro.
    assert results[-1].should_finalize is True
    assert "Birchwood Collision" in results[-1].assistant_text
    assert "advisor" in results[-1].assistant_text.lower()


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
