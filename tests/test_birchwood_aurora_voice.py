"""Tests for the Birchwood "Aurora" voice-naturalness pass (Track A).

Covers the deterministic, transport-neutral naturalness layer added in
``src/verticals/automotive_collision/voice_naturalness.py`` and its wiring
through the shared scripted-intake seams in ``src/twilio/routes.py``:

* per-stage phrasing pools (valid variant, no immediate repeat),
* slot-echo of already-captured fields,
* backchannels keyed to the answered stage,
* off-topic redirection (fires on off-topic, YIELDS to safety/transfer, caps
  then hands off to a callback),
* the persona/disclosure intro,
* and the invariants that MUST hold: the injury question is never softened and
  non-Birchwood sessions (healthcare/insurance) are completely untouched.

Everything here is deterministic — no LLM, no rules/engine/gate change.
"""

from __future__ import annotations

import asyncio

from src.orchestrator.schemas import OrchestratorSession
from src.platform.workflows.schemas import ScriptedStageDefinition
from src.twilio import routes as rt
from src.utils import azure_tts
from src.verticals.automotive_collision import voice_naturalness as vn
from src.verticals.automotive_collision.constants import (
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.automotive_collision.prompts import BIRCHWOOD_COLLISION_INTRO
from src.verticals.automotive_collision.workflow import (
    BirchwoodCollisionIntakeWorkflow,
)

HEALTHCARE_ID = "healthcare_triage_v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _birchwood_session(fields: dict | None = None) -> OrchestratorSession:
    session = OrchestratorSession(session_id="aurora-test", call_sid="CA-test")
    session.workflow_id = BIRCHWOOD_COLLISION_WORKFLOW_ID
    session.channel_metadata["scripted_intake"] = {"fields": dict(fields or {})}
    return session


def _other_session(workflow_id: str = HEALTHCARE_ID) -> OrchestratorSession:
    session = OrchestratorSession(session_id="other", call_sid="CA-other")
    session.workflow_id = workflow_id
    session.channel_metadata["scripted_intake"] = {"fields": {}}
    return session


def _stage(field_name: str, field_type: str = "free_text") -> ScriptedStageDefinition:
    return ScriptedStageDefinition(
        stage_id=field_name.upper(),
        field_name=field_name,
        prompt=f"static prompt for {field_name}",
        field_type=field_type,
    )


def _workflow_intake():
    workflow = BirchwoodCollisionIntakeWorkflow()
    return workflow, workflow.get_scripted_intake_definition()


# ---------------------------------------------------------------------------
# Persona + disclosure intro
# ---------------------------------------------------------------------------


def test_intro_introduces_aurora_and_discloses_automated_assistant():
    intro = BIRCHWOOD_COLLISION_INTRO
    assert "Aurora" in intro
    assert "automated assistant" in intro
    # Disclosure + brand + recording-consent clause all preserved.
    assert "Thank you for calling Birchwood Automotive Group" in intro
    assert "recorded for training and quality purposes" in intro
    # Caller can still reach a human.
    assert "transfer" in intro.lower()


# ---------------------------------------------------------------------------
# Phrasing pool selection
# ---------------------------------------------------------------------------


def test_select_variant_returns_pool_member():
    session = _birchwood_session()
    pool = ["a", "b", "c"]
    for _ in range(20):
        assert vn.select_variant(session, "k", pool) in pool


def test_select_variant_never_repeats_immediately():
    session = _birchwood_session()
    pool = ["a", "b", "c", "d"]
    prev = None
    for _ in range(40):
        choice = vn.select_variant(session, "k", pool)
        assert choice != prev
        prev = choice


def test_select_variant_single_element_pool():
    session = _birchwood_session()
    assert vn.select_variant(session, "k", ["only"]) == "only"


def test_every_pooled_field_has_multiple_variants():
    for field, pool in vn.INTAKE_PHRASING_POOLS.items():
        assert len(pool) >= 3, field
        assert len(set(pool)) == len(pool), f"duplicate variant in {field}"


# ---------------------------------------------------------------------------
# compose_stage_prompt: slot-echo + memoization + gating
# ---------------------------------------------------------------------------


def test_compose_includes_phrasing_variant():
    session = _birchwood_session()
    stage = _stage("vehicle_year", "integer")
    prompt = vn.compose_stage_prompt(session, stage, {})
    # The first prompt of a fresh call may carry the once-per-call hesitation
    # opener as a prefix; the phrasing variant is the suffix.
    assert any(prompt.endswith(v) for v in vn.INTAKE_PHRASING_POOLS["vehicle_year"])


def test_compose_slot_echoes_captured_vehicle():
    session = _birchwood_session()
    fields = {"vehicle_year": 2019, "vehicle_make": "Honda", "vehicle_model": "Civic"}
    stage = _stage("damage_type", "free_text")
    prompt = vn.compose_stage_prompt(session, stage, fields)
    assert "2019 Honda Civic" in prompt
    # The question variant still follows the echo.
    assert any(v in prompt for v in vn.INTAKE_PHRASING_POOLS["damage_type"])


def test_compose_is_memoized_per_stage():
    """Gather renders a stage prompt twice per turn; both must be identical."""
    session = _birchwood_session({"vehicle_year": 2020, "vehicle_make": "Ford"})
    stage = _stage("vehicle_model", "free_text")
    first = vn.compose_stage_prompt(
        session, stage, {"vehicle_year": 2020, "vehicle_make": "Ford"}
    )
    second = vn.compose_stage_prompt(
        session, stage, {"vehicle_year": 2020, "vehicle_make": "Ford"}
    )
    assert first == second


def test_compose_returns_none_without_pool():
    session = _birchwood_session()
    assert vn.compose_stage_prompt(session, _stage("unmapped_field"), {}) is None


def test_compose_returns_none_for_non_birchwood():
    session = _other_session()
    assert (
        vn.compose_stage_prompt(session, _stage("vehicle_year", "integer"), {}) is None
    )


def test_build_dynamic_prompt_uses_pool_for_real_stage():
    workflow, intake = _workflow_intake()
    year_stage = next(s for s in intake.stages if s.field_name == "vehicle_year")
    session = _birchwood_session()
    prompt = workflow.build_dynamic_prompt(session, year_stage)
    assert any(prompt.endswith(v) for v in vn.INTAKE_PHRASING_POOLS["vehicle_year"])


# ---------------------------------------------------------------------------
# Backchannels
# ---------------------------------------------------------------------------


def test_backchannel_birchwood_returns_short_ack():
    session = _birchwood_session()
    ack = vn.backchannel(session, _stage("vehicle_year", "integer"))
    assert ack in vn.BACKCHANNEL_POOLS["short_field"]


def test_backchannel_non_birchwood_is_none():
    session = _other_session()
    assert vn.backchannel(session, _stage("vehicle_year", "integer")) is None


def test_scripted_acknowledgement_birchwood_varies():
    session = _birchwood_session()
    ack = rt._scripted_stage_acknowledgement(_stage("vehicle_year", "integer"), session)
    assert ack in vn.BACKCHANNEL_POOLS["short_field"]


def test_scripted_acknowledgement_legacy_behavior_preserved():
    # No session -> exact legacy behavior (narrative ack, else None).
    narrative = ScriptedStageDefinition(
        stage_id="N",
        field_name="incident_description",
        prompt="x",
        speech_profile="narrative",
    )
    assert rt._scripted_stage_acknowledgement(narrative) == "Got it. I noted that."
    assert rt._scripted_stage_acknowledgement(_stage("vehicle_year", "integer")) is None
    # Non-Birchwood session also keeps legacy behavior.
    assert (
        rt._scripted_stage_acknowledgement(narrative, _other_session())
        == "Got it. I noted that."
    )


# ---------------------------------------------------------------------------
# Injury question safety exception
# ---------------------------------------------------------------------------


def test_injuries_state_variation_is_minimal_and_never_softened():
    variants = vn.INJURIES_STATE_VARIANTS
    assert 1 <= len(variants) <= 2
    for v in variants:
        lowered = v.lower()
        # Every variant directly asks about injury/harm.
        assert "hurt" in lowered or "injured" in lowered or "injury" in lowered
        # Never softened into an optional/skippable question.
        assert "if you'd like" not in lowered
        assert "feel free" not in lowered


# ---------------------------------------------------------------------------
# Off-topic redirection
# ---------------------------------------------------------------------------


def test_redirect_fires_on_off_topic_meta_question():
    session = _birchwood_session()
    stage = _stage("vehicle_year", "integer")
    decision = vn.evaluate_redirect(
        session, stage, "wait, how much is this going to cost?"
    )
    assert decision is not None
    assert decision.advance is False
    assert decision.reprompt is not None
    # Re-asks the current stage's question.
    assert stage.prompt in decision.reprompt


def test_redirect_ignores_on_topic_answer():
    session = _birchwood_session()
    assert (
        vn.evaluate_redirect(session, _stage("vehicle_year", "integer"), "2019") is None
    )


def test_redirect_yields_to_injury_mention():
    session = _birchwood_session()
    stage = _stage("vehicle_year", "integer")
    assert vn.evaluate_redirect(session, stage, "actually my neck really hurts") is None


def test_redirect_yields_to_transfer_request():
    session = _birchwood_session()
    stage = _stage("vehicle_year", "integer")
    assert vn.evaluate_redirect(session, stage, "transfer") is None
    assert (
        vn.evaluate_redirect(session, stage, "can I speak with someone please") is None
    )


def test_redirect_caps_then_hands_off():
    session = _birchwood_session()
    stage = _stage("vehicle_year", "integer")
    first = vn.evaluate_redirect(session, stage, "are you a robot?")
    second = vn.evaluate_redirect(session, stage, "what happens next?")
    third = vn.evaluate_redirect(session, stage, "how long will this take?")
    assert first.advance is False and second.advance is False
    assert third.advance is True
    assert third.reprompt is None
    assert (
        stage.stage_id
        in session.channel_metadata["voice_naturalness"]["redirect_exhausted"]
    )


def test_redirect_none_for_non_birchwood():
    session = _other_session()
    stage = _stage("vehicle_year", "integer")
    assert vn.evaluate_redirect(session, stage, "how much will this cost?") is None


# ---------------------------------------------------------------------------
# Integration through the shared scripted-response handler
# ---------------------------------------------------------------------------


def test_handler_redirects_off_topic_without_recording_field():
    workflow, intake = _workflow_intake()
    session = _birchwood_session()
    rt._initialize_scripted_intake(session, intake)
    year_stage = next(s for s in intake.stages if s.field_name == "vehicle_year")
    session.channel_metadata["stage"] = year_stage.stage_id

    next_stage, reprompt = asyncio.run(
        rt._handle_scripted_stage_response(
            session, intake, year_stage, "how much is this going to cost me?"
        )
    )
    # Stayed on the same stage, spoke a redirect, recorded nothing.
    assert next_stage is year_stage
    assert reprompt is not None
    fields = session.channel_metadata["scripted_intake"]["fields"]
    assert "vehicle_year" not in fields


def test_handler_records_on_topic_answer_and_advances():
    workflow, intake = _workflow_intake()
    session = _birchwood_session()
    rt._initialize_scripted_intake(session, intake)
    year_stage = next(s for s in intake.stages if s.field_name == "vehicle_year")
    session.channel_metadata["stage"] = year_stage.stage_id

    next_stage, reprompt = asyncio.run(
        rt._handle_scripted_stage_response(session, intake, year_stage, "2019")
    )
    assert reprompt is None
    assert next_stage is not None and next_stage.field_name != "vehicle_year"
    fields = session.channel_metadata["scripted_intake"]["fields"]
    assert fields.get("vehicle_year") == 2019


# ---------------------------------------------------------------------------
# Close variation
# ---------------------------------------------------------------------------


def test_close_variants_all_keep_no_promise_disclaimer():
    for variant in vn.CLOSE_VARIANTS:
        # No-promise disclaimer (forbidden-promise guard) + the warm "what
        # happens next" structure the closing test pins — both in every variant.
        assert "doesn't confirm coverage, pricing, or an appointment" in variant
        assert "Here's what happens next" in variant


def test_select_close_birchwood_vs_other():
    assert vn.select_close(_birchwood_session()) in vn.CLOSE_VARIANTS
    assert vn.select_close(_other_session()) == ""


# ---------------------------------------------------------------------------
# Fillers + once-per-call disfluency
# ---------------------------------------------------------------------------


def test_pick_filler_returns_pool_member_no_repeat():
    session = _birchwood_session()
    prev = None
    for _ in range(12):
        choice = vn.pick_filler(session)
        assert choice in vn.FILLER_POOL
        assert choice != prev
        prev = choice


def test_disfluency_applied_at_most_once_per_call():
    session = _birchwood_session()
    _, intake = _workflow_intake()
    stages = {s.field_name: s for s in intake.stages}

    name_prompt = vn.compose_stage_prompt(session, stages["caller_name"], {})
    assert any(name_prompt.startswith(o) for o in vn.DISFLUENCY_OPENERS)

    # A later stage in the same call gets NO second hesitation.
    phone_prompt = vn.compose_stage_prompt(session, stages["phone"], {})
    assert not any(phone_prompt.startswith(o) for o in vn.DISFLUENCY_OPENERS)
    assert session.channel_metadata["voice_naturalness"]["disfluency_used"] is True


# ---------------------------------------------------------------------------
# Expressive SSML micro-realism (Birchwood profile only; healthcare unchanged)
# ---------------------------------------------------------------------------


def _ssml(text: str, expressive: bool) -> str:
    return azure_tts._build_ssml(
        text,
        "en-US-Bree:DragonHDLatestNeural",
        rate="+3%",
        pitch="+0%",
        style=None,
        break_ms=250,
        expressive=expressive,
    )


def test_expressive_ssml_uses_varied_inline_breaks():
    ssml = _ssml("What year is the vehicle? And the make of the car?", True)
    assert "<break time=" in ssml
    # The expressive path drops the single uniform sentence-boundary silence.
    assert "Sentenceboundary" not in ssml
    # Deterministic from the text -> cache-stable.
    assert ssml == _ssml("What year is the vehicle? And the make of the car?", True)


def test_expressive_cadence_varies_between_prompts():
    assert _ssml("What year is the vehicle?", True) != _ssml("And the model?", True)


def test_non_expressive_ssml_is_unchanged_behavior():
    ssml = _ssml("Hello there. How are you?", False)
    assert "Sentenceboundary" in ssml
    assert "<break time=" not in ssml


def test_expressive_ssml_escapes_text():
    ssml = _ssml("Tom & Jerry <hi>, what year?", True)
    assert "&amp;" in ssml
    assert "&lt;hi&gt;" in ssml


def test_cache_key_differs_by_expressive_flag():
    base = ("t", "v", "r", "p", None, 250)
    assert azure_tts._cache_key(*base, False) != azure_tts._cache_key(*base, True)


# ---------------------------------------------------------------------------
# /thinking verbal fillers (Birchwood-gated, graceful typing.wav fallback)
# ---------------------------------------------------------------------------


def test_filler_audio_rejects_bad_names():
    from src.utils import voice_fillers

    assert voice_fillers.get_filler_audio("../../etc/passwd") is None
    assert voice_fillers.get_filler_audio("filler_0.wav") is None
    assert voice_fillers.get_filler_audio("evil.mp3") is None
    # A well-formed name that isn't on disk (no pre-render yet) -> None.
    assert voice_fillers.get_filler_audio("filler_0.mp3") is None


def test_filler_url_none_when_not_pre_rendered():
    # In this environment the fillers are not pre-rendered, so the loop falls
    # back to typing.wav even for a Birchwood session.
    assert rt._birchwood_filler_url(_birchwood_session()) is None
    assert rt._birchwood_filler_url(_other_session()) is None
    assert rt._birchwood_filler_url(None) is None


def test_thinking_loop_falls_back_to_typing():
    twiml = rt._thinking_loop_twiml(_birchwood_session())
    assert "/api/v1/voice/audio/typing.wav" in twiml
    assert "/api/v1/voice/thinking" in twiml
    # Non-Birchwood / no session always uses typing.wav.
    assert "/api/v1/voice/audio/typing.wav" in rt._thinking_loop_twiml(None)


# ---------------------------------------------------------------------------
# A/B harness
# ---------------------------------------------------------------------------


def test_ab_harness_pair_is_aligned_and_changed():
    from scripts.ab_birchwood_voice import OLD_INTRO, SCENARIOS, build_scenario_pair

    old, new = build_scenario_pair(SCENARIOS[0])
    assert len(old) == len(new)  # line-for-line aligned for comparison
    assert old[0] == OLD_INTRO
    assert new[0] == BIRCHWOOD_COLLISION_INTRO
    assert old != new  # the copy actually changed
    # New copy slot-echoes the captured vehicle somewhere.
    assert any("2019 Honda Civic" in line for line in new)
