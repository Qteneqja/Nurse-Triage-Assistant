"""Voice latency + de-stutter regression pack.

Covers the perceived-latency and repeated-response fixes on the gather
transport:

  - one verbal filler per background wait; later hold cycles play a quiet
    typing bed (short for Birchwood) instead of chaining paraphrased hold
    phrases ("Okay, one sec." ... "Just a moment.")
  - Birchwood scripted turns synthesize reply audio BEHIND the hold loop when
    it is not already cached (no more dead-air TTS inside the webhook)
  - the reply TTS is pre-warmed inside the background turn so /thinking can
    deliver instantly
  - /thinking fails closed at a hold-cycle cap instead of looping forever
  - /thinking with no pending task replays the last delivered response
    instead of asking the caller to repeat (which double-processed the turn)
  - deterministic de-stutter helpers: adjacent-paraphrase collapse (safety
    sentences never dropped) and the "Okay. Okay —" leading-word dedupe
  - the injury-overlay advisory is not double-spoken when the reply already
    directs the caller to 911, and is NEVER dropped when the injury mention
    lands on the final scripted answer (Invariant 3)
  - the conversational-tier Aurora intro (disclosure + recording clause) is
    actually spoken by /incoming on the gather transport
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks

from src.platform.workflows.schemas import ResolvedWorkflowRoute
from src.safety.injury_detection import INJURY_SAFETY_ADVISORY
from src.verticals.automotive_collision.constants import (
    AUTOMOTIVE_COLLISION_VERTICAL,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.automotive_collision.voice_naturalness import (
    collapse_adjacent_paraphrases,
    dedupe_leading_stutter,
)

BIRCHWOOD_ROUTE = ResolvedWorkflowRoute(
    vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
    workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
    workflow_version="v1",
)

HEALTHCARE_ROUTE = ResolvedWorkflowRoute(
    vertical_key="healthcare",
    workflow_id="healthcare_triage_v1",
    workflow_version="v1",
)

_TTS_OFF = patch(
    "src.twilio.routes.text_to_speech_url",
    new=AsyncMock(return_value=None),
)


def _setup_memory_repo(mp: pytest.MonkeyPatch):
    from src.platform.workflows.router import reset_workflow_route_resolver
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )

    mp.setattr("src.config.STORAGE_BACKEND", "memory")
    mp.setattr("src.config.ENVIRONMENT", "development")
    mp.setattr("src.config.APP_ENV", "development")
    mp.setattr("src.config.DATABASE_URL", None)
    mp.setattr("src.config.ENABLE_SHARED_NUMBER_VERTICAL_MENU", False)
    mp.setattr("src.config.ENABLE_DEFAULT_WORKFLOW_ROUTE", True)
    mp.setattr("src.config.ENABLE_DEFAULT_HEALTHCARE_ROUTE", True)
    reset_session_repository()
    reset_storage_backend()
    reset_workflow_route_resolver()
    return get_session_repository()


def _birchwood_session(repo, call_sid="CA-VLD-BW"):
    session = repo.create_session(call_sid=call_sid, workflow_route=BIRCHWOOD_ROUTE)
    repo.persist_session(session)
    return session


def _healthcare_session(repo, call_sid="CA-VLD-HC"):
    session = repo.create_session(call_sid=call_sid, workflow_route=HEALTHCARE_ROUTE)
    repo.persist_session(session)
    return session


# ---------------------------------------------------------------------------
# De-stutter helpers
# ---------------------------------------------------------------------------


def test_collapse_drops_adjacent_paraphrase_keeping_later():
    text = (
        "Okay, I've noted the damage to your front bumper. "
        "Got it - I have the front bumper damage noted. "
        "What's the best phone number to reach you?"
    )
    out = collapse_adjacent_paraphrases(text)
    assert "What's the best phone number to reach you?" in out
    # Only ONE of the two near-duplicate sentences survives (the later one).
    assert out.count("noted") == 1
    assert "I have the front bumper damage noted" in out


def test_collapse_keeps_distinct_sentences():
    text = "Thanks, Jane. And what's the best email for your confirmation?"
    assert collapse_adjacent_paraphrases(text) == text


def test_collapse_never_drops_safety_sentences():
    text = (
        "If anyone is hurt, please call 9 1 1 first. "
        "If anyone is injured, please call 911 right away."
    )
    out = collapse_adjacent_paraphrases(text)
    # Both sentences carry safety content — neither may be removed even
    # though they are near-duplicates.
    assert "9 1 1" in out
    assert "911 right away" in out


def test_collapse_handles_empty_and_single_sentence():
    assert collapse_adjacent_paraphrases("") == ""
    assert collapse_adjacent_paraphrases("Just one sentence.") == "Just one sentence."


def test_dedupe_leading_stutter_collapses_doubled_opener():
    assert (
        dedupe_leading_stutter("Okay. Okay — and what's the best phone number?")
        == "Okay — and what's the best phone number?"
    )
    assert (
        dedupe_leading_stutter("Alright. Alright — what year is it?")
        == "Alright — what year is it?"
    )


def test_dedupe_leading_stutter_leaves_normal_text_alone():
    assert (
        dedupe_leading_stutter("Perfect. Okay — what year is it?")
        == "Perfect. Okay — what year is it?"
    )
    assert dedupe_leading_stutter("Okay, one sec.") == "Okay, one sec."
    assert dedupe_leading_stutter("") == ""


# ---------------------------------------------------------------------------
# Hold loop: one verbal filler per wait, then a quiet typing bed
# ---------------------------------------------------------------------------


def test_hold_loop_speaks_filler_once_then_short_bed(monkeypatch):
    from src.twilio import routes as rt

    monkeypatch.setattr(
        "src.utils.voice_fillers.available_filler_files",
        lambda *a, **k: ["filler_0.mp3", "filler_1.mp3"],
    )
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_memory_repo(mp)
        session = _birchwood_session(repo, "CA-HOLD-BW")
        rt._hold_cycles.pop("CA-HOLD-BW", None)

        first = rt._thinking_loop_twiml(session, "CA-HOLD-BW")
        assert "/api/v1/voice/audio/birchwood-fillers/filler_" in first

        second = rt._thinking_loop_twiml(session, "CA-HOLD-BW")
        assert "birchwood-fillers" not in second
        assert "/api/v1/voice/audio/typing-short.wav" in second

        third = rt._thinking_loop_twiml(session, "CA-HOLD-BW")
        assert "/api/v1/voice/audio/typing-short.wav" in third

        # A fresh wait (counter reset) speaks a verbal filler again.
        rt._hold_cycles.pop("CA-HOLD-BW", None)
        fresh = rt._thinking_loop_twiml(session, "CA-HOLD-BW")
        assert "birchwood-fillers" in fresh


def test_hold_loop_healthcare_keeps_classic_typing_bed(monkeypatch):
    from src.twilio import routes as rt

    monkeypatch.setattr(
        "src.utils.voice_fillers.available_filler_files",
        lambda *a, **k: ["filler_0.mp3"],
    )
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_memory_repo(mp)
        session = _healthcare_session(repo, "CA-HOLD-HC")
        rt._hold_cycles.pop("CA-HOLD-HC", None)

        for _ in range(3):
            twiml = rt._thinking_loop_twiml(session, "CA-HOLD-HC")
            assert "/api/v1/voice/audio/typing.wav" in twiml
            assert "birchwood-fillers" not in twiml
            assert "typing-short" not in twiml


def test_hold_loop_without_call_sid_keeps_legacy_behavior():
    from src.twilio import routes as rt

    twiml = rt._thinking_loop_twiml(None)
    assert "/api/v1/voice/audio/typing.wav" in twiml
    assert "/api/v1/voice/thinking" in twiml


# ---------------------------------------------------------------------------
# Scripted hold gating + background TwiML build
# ---------------------------------------------------------------------------


def test_scripted_hold_not_needed_without_azure_key(monkeypatch):
    from src.twilio import routes as rt

    monkeypatch.setattr("src.config.AZURE_SPEECH_KEY", "")
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_memory_repo(mp)
        bw = _birchwood_session(repo, "CA-PROBE-1")
        # Polly <Say> renders inline instantly — no hold.
        assert rt._scripted_hold_needed(bw, "What year is the vehicle?") is False


def test_scripted_hold_needed_only_for_birchwood_cache_miss(monkeypatch):
    from src.twilio import routes as rt

    monkeypatch.setattr("src.config.AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setattr("src.utils.azure_tts.get_cached_tts_url", lambda *a, **k: None)
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_memory_repo(mp)
        bw = _birchwood_session(repo, "CA-PROBE-2")
        hc = _healthcare_session(repo, "CA-PROBE-3")
        assert rt._scripted_hold_needed(bw, "What year is the vehicle?") is True
        # Healthcare always keeps the inline path.
        assert rt._scripted_hold_needed(hc, "When did this start?") is False

    monkeypatch.setattr(
        "src.utils.azure_tts.get_cached_tts_url",
        lambda *a, **k: "https://blob/cached.mp3",
    )
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_memory_repo(mp)
        bw = _birchwood_session(repo, "CA-PROBE-4")
        # Cached audio → speak inline, no hold.
        assert rt._scripted_hold_needed(bw, "What year is the vehicle?") is False


@pytest.mark.asyncio
async def test_background_twiml_build_delivers_via_thinking():
    from src.twilio import routes as rt

    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        session = _birchwood_session(repo, "CA-BGTWIML")

        async def _twiml():
            return "<Response><Say>ready</Say></Response>"

        hold = rt._begin_background_twiml("CA-BGTWIML", session, _twiml())
        assert "/api/v1/voice/thinking" in hold
        assert "CA-BGTWIML" in rt._pending_turns

        task, _ = rt._pending_turns["CA-BGTWIML"]
        await task

        response = await rt.handle_thinking(
            SimpleNamespace(headers={}), BackgroundTasks(), CallSid="CA-BGTWIML"
        )
        assert b"ready" in response.body
        assert "CA-BGTWIML" not in rt._pending_turns
        # The delivered response is remembered for idempotent replay.
        stored = repo.load_session_by_call("CA-BGTWIML")
        from src.twilio import webhook_stability as stability

        assert stability.last_gather_twiml(stored) is not None
        assert "ready" in stability.last_gather_twiml(stored)


# ---------------------------------------------------------------------------
# /thinking: loop cap fails closed; no-pending replays the last response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thinking_hold_cap_fails_closed_for_birchwood():
    from src.twilio import routes as rt

    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        session = _birchwood_session(repo, "CA-HOLDCAP")

        never_done: asyncio.Future = asyncio.get_event_loop().create_future()

        async def _hang():
            await never_done

        task = asyncio.create_task(_hang())
        rt._pending_turns["CA-HOLDCAP"] = (task, session)
        rt._hold_cycles["CA-HOLDCAP"] = rt._MAX_HOLD_CYCLES

        response = await rt.handle_thinking(
            SimpleNamespace(headers={}), BackgroundTasks(), CallSid="CA-HOLDCAP"
        )
        body = response.body.decode()
        assert "<Hangup/>" in body
        assert "call you back" in body  # non-clinical fail-closed promise
        assert "CA-HOLDCAP" not in rt._pending_turns
        assert task.cancelled() or task.cancelling()
        never_done.cancel()


@pytest.mark.asyncio
async def test_thinking_no_pending_replays_last_response():
    from src.twilio import routes as rt
    from src.twilio import webhook_stability as stability

    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        session = _birchwood_session(repo, "CA-REPLAY")
        question_twiml = "<Response><Say>And the make of the vehicle?</Say></Response>"
        stability.note_gather_input(session, "fp-test")
        stability.remember_gather_twiml(session, question_twiml)
        repo.persist_session(session)

        response = await rt.handle_thinking(
            SimpleNamespace(headers={}), BackgroundTasks(), CallSid="CA-REPLAY"
        )
        assert b"And the make of the vehicle?" in response.body
        assert b"Sorry about the wait" not in response.body


@pytest.mark.asyncio
async def test_thinking_no_pending_never_replays_a_hold_redirect():
    from src.twilio import routes as rt
    from src.twilio import webhook_stability as stability

    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        session = _birchwood_session(repo, "CA-REPLAY2")
        stability.note_gather_input(session, "fp-test-2")
        stability.remember_gather_twiml(session, rt._thinking_loop_twiml(None))
        repo.persist_session(session)

        response = await rt.handle_thinking(
            SimpleNamespace(headers={}), BackgroundTasks(), CallSid="CA-REPLAY2"
        )
        # Replaying a hold-redirect would loop the call — fall back to the
        # apology re-gather instead.
        assert b"Sorry about the wait" in response.body


# ---------------------------------------------------------------------------
# Duplicate-gather replay: tight window, short answers exempt
# ---------------------------------------------------------------------------


def test_duplicate_replay_skips_short_answers():
    from src.orchestrator.schemas import ConversationTurn
    from src.twilio import webhook_stability as stability

    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_memory_repo(mp)
        session = _birchwood_session(repo, "CA-DUPSHORT")
        fp = stability.gather_input_fingerprint("CA-DUPSHORT", "Yes.", None)
        stability.note_gather_input(session, fp)
        stability.remember_gather_twiml(session, "<Response/>")
        session.conversation.append(ConversationTurn(role="caller", text="Yes."))
        # Identical short answer to the NEXT question must NOT be treated as
        # a redelivery (it used to replay the prior prompt and eat the answer).
        assert stability.duplicate_gather_replay(session, fp, "Yes.") is None


def test_duplicate_replay_still_works_for_long_answers():
    from src.orchestrator.schemas import ConversationTurn
    from src.twilio import webhook_stability as stability

    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_memory_repo(mp)
        session = _birchwood_session(repo, "CA-DUPLONG")
        speech = "yes it is safe to drive"
        fp = stability.gather_input_fingerprint("CA-DUPLONG", speech, None)
        stability.note_gather_input(session, fp)
        stability.remember_gather_twiml(session, "<Response>orig</Response>")
        session.conversation.append(ConversationTurn(role="caller", text=speech))
        assert (
            stability.duplicate_gather_replay(session, fp, speech)
            == "<Response>orig</Response>"
        )


# ---------------------------------------------------------------------------
# Injury overlay: no double-911, and the advisory is never dropped
# ---------------------------------------------------------------------------


def _turn_result(assistant_text, session_state):
    from src.platform.workflows.schemas import WorkflowTurnResult

    return WorkflowTurnResult(
        assistant_text=assistant_text,
        stage="DYNAMIC",
        should_continue=True,
        should_finalize=False,
        escalation_required=False,
        updated_state=session_state,
    )


def _overlay(result, user_text):
    from src.platform.workflows.router import _enforce_turn_safety_overlay
    from src.platform.workflows.schemas import WorkflowContext, WorkflowInput

    context = WorkflowContext(
        workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
        workflow_version="v1",
        vertical=AUTOMOTIVE_COLLISION_VERTICAL,
        session_id="s-overlay",
    )
    workflow_input = WorkflowInput(user_text=user_text, session_state={})
    return _enforce_turn_safety_overlay(context, workflow_input, result)


def test_overlay_skips_canned_advisory_when_reply_directs_911():
    state = {"conversation": [], "channel_metadata": {"stage": "DYNAMIC"}}
    result = _turn_result(
        "I'm so sorry - if anyone's hurt, please call 911 right away. "
        "Now, what's the best number to reach you?",
        state,
    )
    out = _overlay(result, "my passenger got hurt in the crash")
    assert INJURY_SAFETY_ADVISORY not in out.assistant_text
    # Marked delivered so a later turn doesn't re-deliver it as a paraphrase.
    stability_state = out.updated_state["channel_metadata"]["webhook_stability"]
    assert stability_state["injury_advisory_given"]


def test_overlay_still_prepends_advisory_when_reply_lacks_directive():
    state = {"conversation": [], "channel_metadata": {"stage": "DYNAMIC"}}
    result = _turn_result("Thanks. What's the best number to reach you?", state)
    out = _overlay(result, "my passenger got hurt in the crash")
    assert out.assistant_text.startswith(INJURY_SAFETY_ADVISORY)
    stability_state = out.updated_state["channel_metadata"]["webhook_stability"]
    assert stability_state["injury_advisory_given"]


def test_unmark_injury_advisory_rearms_the_advisory():
    from src.twilio import webhook_stability as stability

    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_memory_repo(mp)
        session = _birchwood_session(repo, "CA-REARM")
        stability.mark_injury_advisory_given(session)
        assert stability.injury_advisory_already_given(session)
        stability.unmark_injury_advisory(session)
        assert not stability.injury_advisory_already_given(session)
        # And the text-scan path fires again after re-arming.
        advisory = stability.injury_advisory_if_needed(
            session, "my neck hurts a little"
        )
        assert advisory == INJURY_SAFETY_ADVISORY


@pytest.mark.asyncio
async def test_injury_on_final_scripted_answer_is_spoken_not_dropped():
    """Invariant 3: an injury mention in the answer to the LAST scripted stage
    must still produce a spoken advisory on the finalize message (it used to
    be computed, marked given, and then silently dropped on the fall-through
    to the dynamic finalize)."""
    from src.twilio import routes as rt
    from src.verticals.automotive_collision.workflow import (
        BirchwoodCollisionIntakeWorkflow,
    )

    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
        last_stage = intake.stages[-1]
        session = repo.create_session(
            call_sid="CA-INJ-LAST", workflow_route=BIRCHWOOD_ROUTE
        )
        # Prefill every stage except the last so answering it completes intake.
        fields = {}
        for stage in intake.stages[:-1]:
            fields[stage.store_as or stage.field_name] = "prefilled value"
        session.channel_metadata["stage"] = last_stage.stage_id
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": len(intake.stages) - 1,
            "current_stage_id": last_stage.stage_id,
            "fields": fields,
            "attempts": {},
            "completed": False,
        }
        repo.persist_session(session)

        response = await rt.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-INJ-LAST",
            SpeechResult="no claim number yet, and my neck hurts a little",
        )
        body = response.body.decode()

        if "CA-INJ-LAST" in rt._pending_turns:
            # Fall-through to the dynamic finalize: wait for the background
            # turn, then deliver via /thinking.
            task, _ = rt._pending_turns["CA-INJ-LAST"]
            await task
            response = await rt.handle_thinking(
                SimpleNamespace(headers={}),
                BackgroundTasks(),
                CallSid="CA-INJ-LAST",
            )
            body = response.body.decode()

        assert "9 1 1" in body, (
            "Injury advisory must be spoken when the mention arrives on the "
            f"final scripted answer. Body was: {body[:400]}"
        )


# ---------------------------------------------------------------------------
# Conversational-tier intro is spoken on the gather transport
# ---------------------------------------------------------------------------


def test_split_trailing_question():
    from src.twilio.routes import _split_trailing_question
    from src.verticals.automotive_collision.prompts import (
        BIRCHWOOD_COLLISION_CONVERSATIONAL_INTRO,
    )

    preamble, question = _split_trailing_question(
        BIRCHWOOD_COLLISION_CONVERSATIONAL_INTRO
    )
    assert question.endswith("?")
    assert "full name" in question
    assert "recorded for training" in preamble
    # No trailing question → everything is the gather prompt.
    assert _split_trailing_question("Hello there.") == ("", "Hello there.")


@pytest.mark.asyncio
async def test_incoming_speaks_conversational_intro(monkeypatch):
    from src.twilio import routes as rt

    monkeypatch.setattr("src.config.BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)

        class _StubResolver:
            def resolve(self, called_phone_number=None):
                return BIRCHWOOD_ROUTE

        mp.setattr(rt, "get_workflow_route_resolver", lambda: _StubResolver())

        response = await rt.handle_incoming_call(
            SimpleNamespace(headers={}), CallSid="CA-CONV-INTRO", To=None
        )
        body = response.body.decode()
        # The Aurora intro (disclosure + recording clause) must be spoken,
        # not just logged: it used to be replaced by a generic fallback.
        assert "recorded for training" in body
        assert "full name" in body
        assert "How can I help you today?" not in body
        # The transcript logs the intro exactly once.
        stored = repo.load_session_by_call("CA-CONV-INTRO")
        intro_turns = [
            t for t in stored.conversation if "recorded for training" in (t.text or "")
        ]
        assert len(intro_turns) == 1


# ---------------------------------------------------------------------------
# Reply TTS pre-warm inside the background turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_turn_prewarms_reply_tts():
    from src.twilio import routes as rt

    tts_mock = AsyncMock(return_value=None)
    with (
        pytest.MonkeyPatch.context() as mp,
        patch.object(rt, "_tts_audio_url", tts_mock),
    ):
        repo = _setup_memory_repo(mp)
        session = _birchwood_session(repo, "CA-PREWARM")
        session.channel_metadata["stage"] = "DYNAMIC"
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": None,
            "current_stage_id": "DYNAMIC",
            "fields": {},
            "attempts": {},
            "completed": True,
        }
        repo.persist_session(session)

        await rt.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-PREWARM",
            SpeechResult="that's everything I can tell you about the crash",
        )
        assert "CA-PREWARM" in rt._pending_turns
        task, _ = rt._pending_turns["CA-PREWARM"]
        result, updated = await task
        # The background task pre-warmed the TTS cache with the reply text.
        spoken_texts = [c.args[0] for c in tts_mock.await_args_list if c.args]
        assert result["message"] in spoken_texts
        rt._pending_turns.pop("CA-PREWARM", None)
        rt._hold_cycles.pop("CA-PREWARM", None)


# ---------------------------------------------------------------------------
# Short typing bed route + config plumb-through
# ---------------------------------------------------------------------------


def test_short_typing_bed_is_served_and_shorter():
    from fastapi.testclient import TestClient

    from src.main import app
    from src.utils.typing_sound import get_short_typing_sound_wav, get_typing_sound_wav

    assert len(get_short_typing_sound_wav()) < len(get_typing_sound_wav())
    client = TestClient(app)
    response = client.get("/api/v1/voice/audio/typing-short.wav")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content[:4] == b"RIFF"


def test_birchwood_speech_timeout_is_env_tunable(monkeypatch):
    from src.verticals.automotive_collision.workflow import (
        _speech_settings_for_stage,
    )

    profile, _timeout, speech_timeout = _speech_settings_for_stage(
        field_name="vehicle_year"
    )
    assert profile == "short_field"
    assert speech_timeout == "3"  # default unchanged

    monkeypatch.setattr("src.config.BIRCHWOOD_SHORT_FIELD_SPEECH_TIMEOUT", "auto")
    _, _, speech_timeout = _speech_settings_for_stage(field_name="vehicle_year")
    assert speech_timeout == "auto"
