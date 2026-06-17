"""PR 1 — Runtime Stability Gate regression pack.

Covers the explicit failure-mode contracts from PR 1:
  - duplicate/repeated Twilio webhooks never double-process or corrupt state
  - missing/empty SpeechResult and caller silence have tested behaviors,
    with a fail-safe close after repeated silence
  - a finalized session receiving another webhook returns a safe terminal
    response and never reopens
  - storage failure mid-call fails closed (apology + callback promise +
    flagged record for non-clinical verticals; healthcare wording unchanged)
  - long narratives are captured across segments with no loss, and the
    injury safety branch (Invariant 3) fires deterministically
  - red-flag detection does not weaken with utterance length (Invariant 1)
  - production config enforces the Postgres backend
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks

from src.platform.workflows.schemas import ResolvedWorkflowRoute
from src.safety.injury_detection import (
    INJURY_SAFETY_ADVISORY,
    scan_for_injuries,
)
from src.safety.red_flags import check_all, score_red_flags
from src.verticals.automotive_collision.constants import (
    AUTOMOTIVE_COLLISION_VERTICAL,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.automotive_collision.workflow import (
    BirchwoodCollisionIntakeWorkflow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

LONG_COLLISION_STORY = (
    "So I was driving home from my daughter's soccer practice on Tuesday "
    "evening, it was raining pretty hard and the light had just turned "
    "green, and I started going through the intersection at Portage and "
    "Main when this blue pickup truck ran the red light and clipped my "
    "front end, and then I spun around and the back corner hit the median, "
    "and there was glass everywhere from the headlights, and the other "
    "driver pulled over and we exchanged information, and a police officer "
    "came by about twenty minutes later and took a report, "
) * 4  # ~2000 chars — a genuinely long story


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
    # Pin routing flags — earlier suites may leave them disabled.
    mp.setattr("src.config.ENABLE_DEFAULT_WORKFLOW_ROUTE", True)
    mp.setattr("src.config.ENABLE_DEFAULT_HEALTHCARE_ROUTE", True)
    reset_session_repository()
    reset_storage_backend()
    # The route resolver caches a repository built against the previous
    # storage backend — reset it so default routing works after the swap.
    reset_workflow_route_resolver()
    return get_session_repository()


def _birchwood_session_at_stage(repo, call_sid: str, field_name: str):
    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    stage = next(s for s in intake.stages if s.field_name == field_name)
    session = repo.create_session(call_sid=call_sid, workflow_route=BIRCHWOOD_ROUTE)
    session.channel_metadata["stage"] = stage.stage_id
    session.channel_metadata["scripted_intake"] = {
        "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
        "current_index": intake.stages.index(stage),
        "current_stage_id": stage.stage_id,
        "fields": {},
        "attempts": {},
        "completed": False,
    }
    repo.persist_session(session)
    return session, stage


async def _gather(call_sid: str, speech: str | None, digits: str | None = None):
    from src.twilio import routes as twilio_routes

    return await twilio_routes.handle_gather(
        SimpleNamespace(headers={}),
        BackgroundTasks(),
        CallSid=call_sid,
        SpeechResult=speech,
        Digits=digits,
    )


_TTS_OFF = patch(
    "src.twilio.routes.text_to_speech_url",
    new=AsyncMock(return_value=None),
)


# ---------------------------------------------------------------------------
# Injury detection (Invariant 3 — deterministic layer)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "my neck hurts and I have a headache",
        "I think my passenger is injured",
        "there was blood on the steering wheel",
        "the paramedics checked us out",
        "I got whiplash from the impact",
        "we had to call 911",
        "my arm is killing me, I might go to the hospital",
        "I blacked out for a second after the airbag",
        "my kid hit his head on the window",
    ],
)
def test_injury_scan_detects_mentions(text):
    result = scan_for_injuries(text)
    assert result.mentioned is True


@pytest.mark.parametrize(
    "text",
    [
        "no one was hurt, just the bumper",
        "nobody was injured thankfully",
        "we're all fine, the car took the worst of it",
        "no injuries, just body damage",
        "I wasn't hurt at all",
        "the windshield is broken but everyone is okay",
    ],
)
def test_injury_scan_honors_denials(text):
    result = scan_for_injuries(text)
    assert result.mentioned is False
    assert result.denied is True


@pytest.mark.parametrize(
    "text",
    [
        "the bumper fell off and the door is dented",
        "broken windshield and a flat tire",
        "just a scratch on the paint",
        "",
    ],
)
def test_injury_scan_ignores_vehicle_damage(text):
    result = scan_for_injuries(text)
    assert result.mentioned is False


def test_injury_scan_mention_survives_partial_denial():
    # A denial about one person never suppresses a separate positive mention.
    result = scan_for_injuries("my kids are fine but my neck hurts a lot")
    assert result.mentioned is True
    assert result.denied is True


def test_injury_mention_buried_in_long_story_still_detected():
    story = LONG_COLLISION_STORY + " and honestly my shoulder has been sore since."
    result = scan_for_injuries(story)
    assert result.mentioned is True


# ---------------------------------------------------------------------------
# Red-flag detection must not weaken with utterance length (Invariant 1)
# ---------------------------------------------------------------------------

LONG_CLINICAL_RAMBLE = (
    "Well let me tell you, it started last week when I was gardening, and "
    "then my cousin visited from Brandon and we cooked a big dinner, and I "
    "have been a little tired but I thought it was just the weather, and "
    "my knee has been acting up like it always does in the spring, "
) * 12  # ~3300 chars of benign rambling


def test_red_flag_detected_at_end_of_long_utterance():
    utterance = LONG_CLINICAL_RAMBLE + " and now I have crushing chest pain."
    disposition, score, flag_ids = score_red_flags(utterance=utterance)
    assert disposition == "ER_NOW"
    assert flag_ids


def test_red_flag_detected_in_middle_of_long_utterance():
    utterance = (
        LONG_CLINICAL_RAMBLE + " and I can't breathe properly, " + LONG_CLINICAL_RAMBLE
    )
    result = check_all(utterance)
    assert result.triggered is True


def test_red_flag_scoring_identical_short_vs_long_context():
    short = "I have crushing chest pain."
    long = LONG_CLINICAL_RAMBLE + " I have crushing chest pain."
    short_disp, _, short_ids = score_red_flags(utterance=short)
    long_disp, _, long_ids = score_red_flags(utterance=long)
    assert short_disp == long_disp == "ER_NOW"
    assert set(short_ids) <= set(long_ids)


# ---------------------------------------------------------------------------
# Production config enforces Postgres
# ---------------------------------------------------------------------------


def test_validate_config_rejects_memory_backend_with_legacy_environment_production():
    import src.config as config

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "ENVIRONMENT", "production")
        mp.setattr(config, "APP_ENV", "development")
        mp.setattr(config, "STORAGE_BACKEND", "memory")
        mp.setattr(config, "DATABASE_URL", None)
        errors = config.validate_config()
    assert any("Postgres" in e for e in errors)


def test_storage_factory_refuses_memory_in_production():
    from src.storage.factory import get_storage_backend, reset_storage_backend

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.config.ENVIRONMENT", "production")
        mp.setattr("src.config.APP_ENV", "production")
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        reset_storage_backend()
        with pytest.raises(RuntimeError):
            get_storage_backend()
    reset_storage_backend()


# ---------------------------------------------------------------------------
# Webhook behaviors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_incoming_webhook_does_not_create_second_session():
    from src.twilio import routes as twilio_routes

    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        request = SimpleNamespace(headers={})

        first = await twilio_routes.handle_incoming_call(
            request, CallSid="CA-DUP-INCOMING", To=None
        )
        assert repo.get_active_session_count() == 1

        second = await twilio_routes.handle_incoming_call(
            request, CallSid="CA-DUP-INCOMING", To=None
        )
        assert repo.get_active_session_count() == 1
        assert second.body == first.body


@pytest.mark.asyncio
async def test_duplicate_gather_webhook_replays_without_double_processing():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _birchwood_session_at_stage(repo, "CA-DUP-GATHER", "is_drivable")

        first = await _gather("CA-DUP-GATHER", "yes it is safe to drive")
        second = await _gather("CA-DUP-GATHER", "yes it is safe to drive")

        assert second.body == first.body
        stored = repo.load_session_by_call("CA-DUP-GATHER")
        caller_turns = [
            t.text
            for t in stored.conversation
            if t.role == "caller" and t.text == "yes it is safe to drive"
        ]
        assert len(caller_turns) == 1


@pytest.mark.asyncio
async def test_empty_speech_reprompts_without_advancing_stage():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _, stage = _birchwood_session_at_stage(repo, "CA-EMPTY", "is_drivable")

        response = await _gather("CA-EMPTY", None)
        body = response.body.decode().lower()
        assert "didn't catch that" in body
        stored = repo.load_session_by_call("CA-EMPTY")
        assert stored.channel_metadata["stage"] == stage.stage_id
        assert not stored.is_finalized


@pytest.mark.asyncio
async def test_repeated_silence_closes_birchwood_call_fail_safe():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _birchwood_session_at_stage(repo, "CA-SILENT-BW", "is_drivable")

        await _gather("CA-SILENT-BW", None)
        await _gather("CA-SILENT-BW", "")
        final = await _gather("CA-SILENT-BW", None)

        body = final.body.decode()
        assert "call you back" in body
        assert "<Hangup/>" in body
        stored = repo.load_session_by_call("CA-SILENT-BW")
        assert stored.is_finalized is True
        assert stored.finalization_reason == "caller_silence"
        flags = stored.channel_metadata["webhook_stability"]["flags"]
        assert "incomplete" in flags


@pytest.mark.asyncio
async def test_repeated_silence_closes_healthcare_call_with_911_guidance():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        session = repo.create_session(
            call_sid="CA-SILENT-HC", workflow_route=HEALTHCARE_ROUTE
        )
        repo.persist_session(session)

        await _gather("CA-SILENT-HC", None)
        await _gather("CA-SILENT-HC", None)
        final = await _gather("CA-SILENT-HC", None)

        body = final.body.decode()
        assert "9 1 1" in body
        assert "<Hangup/>" in body
        stored = repo.load_session_by_call("CA-SILENT-HC")
        assert stored.is_finalized is True


@pytest.mark.asyncio
async def test_speech_resets_silence_counter():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _birchwood_session_at_stage(repo, "CA-RESET", "vehicle_make")

        await _gather("CA-RESET", None)
        await _gather("CA-RESET", None)
        await _gather("CA-RESET", "Toyota")  # speech resets the counter
        response = await _gather("CA-RESET", None)

        body = response.body.decode().lower()
        assert "didn't catch that" in body  # reprompt, NOT a fail-safe close
        stored = repo.load_session_by_call("CA-RESET")
        assert not stored.is_finalized


@pytest.mark.asyncio
async def test_webhook_to_finalized_session_returns_safe_terminal_response():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        session, _ = _birchwood_session_at_stage(repo, "CA-FINALIZED", "is_drivable")
        session.is_finalized = True
        repo.persist_session(session)

        response = await _gather("CA-FINALIZED", "hello are you still there")
        body = response.body.decode()
        assert "already been completed" in body
        assert "<Hangup/>" in body
        stored = repo.load_session_by_call("CA-FINALIZED")
        assert stored.is_finalized is True
        # No new caller turns were appended to the finalized session.
        assert all(t.text != "hello are you still there" for t in stored.conversation)


@pytest.mark.asyncio
async def test_gather_with_no_session_recovers_instead_of_crashing():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)

        response = await _gather("CA-GHOST", "my chest hurts")
        body = response.body.decode()
        assert response.status_code == 200
        assert "<Gather" in body or "<Hangup/>" in body
        # Recovery created a continuation session for the call.
        assert repo.load_session_by_call("CA-GHOST") is not None


@pytest.mark.asyncio
async def test_storage_failure_mid_call_fails_closed_with_callback_promise():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _birchwood_session_at_stage(repo, "CA-DB-DOWN", "vehicle_make")

        def _boom(_session):
            raise RuntimeError("database unavailable")

        mp.setattr(repo, "persist_session", _boom)
        response = await _gather("CA-DB-DOWN", "Toyota")

        body = response.body.decode()
        assert response.status_code == 200
        assert "call you back" in body
        assert "<Hangup/>" in body


@pytest.mark.asyncio
async def test_storage_failure_healthcare_keeps_original_wording():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        session = repo.create_session(
            call_sid="CA-DB-DOWN-HC", workflow_route=HEALTHCARE_ROUTE
        )
        repo.persist_session(session)

        def _boom(_session):
            raise RuntimeError("database unavailable")

        mp.setattr(repo, "persist_session", _boom)
        response = await _gather("CA-DB-DOWN-HC", "I have a cough")

        body = response.body.decode()
        assert response.status_code == 200
        assert "Sorry, we encountered an error. Please try again later." in body


# ---------------------------------------------------------------------------
# Injury safety advisory on the dynamic/final turn (shared platform overlay)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_injury_first_mentioned_in_final_turn_still_gets_spoken_advisory():
    """The /thinking fallback must speak the advisory when the injury mention
    arrives only in the final DYNAMIC-stage utterance (review finding: the
    scripted-path marking used to swallow it)."""
    from unittest.mock import MagicMock

    from src.platform.workflows.schemas import WorkflowTurnResult
    from src.twilio import routes as twilio_routes

    call_sid = "CA-INJURY-FINAL-TURN"
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        session = repo.create_session(call_sid=call_sid, workflow_route=BIRCHWOOD_ROUTE)
        session.channel_metadata["stage"] = "DYNAMIC"
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": None,
            "current_stage_id": "DYNAMIC",
            "fields": {"incident_description": "minor scrape in a lot"},
            "attempts": {},
            "completed": True,
        }
        repo.persist_session(session)

        engine = MagicMock()

        async def _final_turn(context, workflow_input):
            updated = repo.load_session_by_call(call_sid)
            updated.channel_metadata["workflow_final_result"] = {
                "final_disposition": "COMPLETED_INTAKE",
                "confidence_score": 0.88,
                "summary": "intake",
                "structured_output": {
                    "intake_record": {"flags": ["injuries_reported"]}
                },
                "safety_events": [],
                "rules_triggered": ["automotive_collision:injury_safety_branch"],
                "audit_metadata": {},
            }
            return WorkflowTurnResult(
                assistant_text="Thanks, I have the main details noted.",
                stage="FINAL",
                should_continue=False,
                should_finalize=True,
                escalation_required=False,
                updated_state=updated.model_dump(mode="json"),
            )

        engine.handle_turn = _final_turn
        mp.setattr(twilio_routes, "get_workflow_engine", lambda: engine)

        gather_response = await _gather(
            call_sid, "actually now that you ask, my neck hurts a bit"
        )
        # DYNAMIC path returns typing sounds, never the advisory directly.
        assert "9 1 1" not in gather_response.body.decode()

        task, _ = twilio_routes._pending_turns[call_sid]
        await task  # let the workflow turn finish

        thinking_response = await twilio_routes.handle_thinking(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid=call_sid,
        )
        body = thinking_response.body.decode()
        assert "9 1 1" in body  # advisory prepended to the closing message
        assert "Thanks, I have the main details noted." in body


def test_injury_advisory_text_gives_no_medical_advice_beyond_911():
    lowered = INJURY_SAFETY_ADVISORY.lower()
    assert "9 1 1" in lowered
    for forbidden in ("diagnos", "medication", "dosage", "treat", "symptom"):
        assert forbidden not in lowered
