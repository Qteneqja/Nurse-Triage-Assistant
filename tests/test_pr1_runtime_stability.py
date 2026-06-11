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
from src.twilio import webhook_stability as stability
from src.verticals.automotive_collision.constants import (
    AUTOMOTIVE_COLLISION_VERTICAL,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.automotive_collision.rules import classify_collision_intake
from src.verticals.automotive_collision.schemas import AutomotiveCollisionIntake
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
# Birchwood rules: injury overlay applies to every routing outcome
# ---------------------------------------------------------------------------


def _complete_intake(**overrides) -> AutomotiveCollisionIntake:
    base = dict(
        caller_name="Pat Caller",
        phone="+12045550199",
        email="pat@example.com",
        address="12 Main St",
        vehicle_year=2021,
        vehicle_make="Toyota",
        vehicle_model="Corolla",
        license_plate="ABC 123",
        is_drivable=True,
        damage_type="rear bumper body damage",
        incident_description="Rear-ended at a stop light. No one was hurt.",
        filing_insurance_claim=True,
        claim_number="CLM-1234",
        preferred_collision_center="BIRCHWOOD_COLLISION_LOCATION_1",
        is_rebuilt_or_salvage=False,
    )
    base.update(overrides)
    return AutomotiveCollisionIntake(**base)


def test_injury_flag_on_completed_intake():
    intake = _complete_intake(
        incident_description="Rear-ended at a light and my neck hurts since."
    )
    assessment = classify_collision_intake(intake)
    assert assessment.flags[0] == "injuries_reported"
    assert assessment.human_review_required is True
    assert "automotive_collision:injury_safety_branch" in assessment.rules_triggered


def test_injury_flag_on_transfer_outcome():
    intake = _complete_intake(
        is_drivable=False,
        incident_description="Car won't start and my back is sore.",
    )
    assessment = classify_collision_intake(intake)
    assert assessment.outcome == "TRANSFER_COLLISION_CENTER"
    assert "injuries_reported" in assessment.flags
    assert assessment.human_review_required is True


def test_injury_denial_recorded_without_forcing_review():
    intake = _complete_intake(
        incident_description="Fender bender, no one was hurt at all."
    )
    assessment = classify_collision_intake(intake)
    assert "injuries_denied" in assessment.flags
    assert "injuries_reported" not in assessment.flags
    assert assessment.outcome == "COMPLETED_INTAKE"


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
# Long-story narrative capture (1B)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_narrative_captured_across_segments_without_loss():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _birchwood_session_at_stage(repo, "CA-LONG-STORY", "incident_description")

        seg1 = "I was heading north on Pembina when a truck merged into my lane"
        seg2 = "and it caught the front fender and pushed me onto the shoulder"
        seg3 = "the police came and took a report, no one was hurt"

        r1 = await _gather("CA-LONG-STORY", seg1)
        assert "go on, i'm listening" in r1.body.decode().lower()
        r2 = await _gather("CA-LONG-STORY", seg2)
        assert "go on, i'm listening" in r2.body.decode().lower()
        r3 = await _gather("CA-LONG-STORY", seg3)
        assert "go on, i'm listening" in r3.body.decode().lower()
        r4 = await _gather("CA-LONG-STORY", "that's everything")

        stored = repo.load_session_by_call("CA-LONG-STORY")
        narrative = stored.channel_metadata["scripted_intake"]["fields"][
            "incident_description"
        ]
        assert narrative == f"{seg1} {seg2} {seg3}"
        # Next stage was prompted — the caller was not cut off nor stuck.
        assert "<Gather" in r4.body.decode()


@pytest.mark.asyncio
async def test_narrative_trailing_cue_completes_and_is_stripped():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _birchwood_session_at_stage(repo, "CA-TRAIL-CUE", "incident_description")

        await _gather("CA-TRAIL-CUE", "Someone backed into me in a parking lot")
        await _gather("CA-TRAIL-CUE", "the rear door is dented and that's everything")

        stored = repo.load_session_by_call("CA-TRAIL-CUE")
        narrative = stored.channel_metadata["scripted_intake"]["fields"][
            "incident_description"
        ]
        assert narrative == (
            "Someone backed into me in a parking lot the rear door is dented"
        )


@pytest.mark.asyncio
async def test_silence_after_story_completes_narrative_not_a_strike():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _birchwood_session_at_stage(repo, "CA-PAUSE", "incident_description")

        await _gather("CA-PAUSE", "I hit a deer on the highway near Selkirk")
        response = await _gather("CA-PAUSE", None)  # caller done — silence

        stored = repo.load_session_by_call("CA-PAUSE")
        narrative = stored.channel_metadata["scripted_intake"]["fields"][
            "incident_description"
        ]
        assert narrative == "I hit a deer on the highway near Selkirk"
        assert not stored.is_finalized
        assert "<Gather" in response.body.decode()  # moved on to next question


@pytest.mark.asyncio
async def test_very_long_single_utterance_passes_through_unmodified():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _birchwood_session_at_stage(repo, "CA-VERBATIM", "incident_description")

        await _gather("CA-VERBATIM", LONG_COLLISION_STORY)
        await _gather("CA-VERBATIM", "that's all")

        stored = repo.load_session_by_call("CA-VERBATIM")
        narrative = stored.channel_metadata["scripted_intake"]["fields"][
            "incident_description"
        ]
        assert LONG_COLLISION_STORY.strip() in narrative
        assert len(narrative) >= len(LONG_COLLISION_STORY.strip())


@pytest.mark.asyncio
async def test_injury_mention_in_narrative_triggers_spoken_advisory_once():
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _birchwood_session_at_stage(repo, "CA-INJURY", "incident_description")

        r1 = await _gather("CA-INJURY", "A car hit my door and my neck hurts")
        body1 = r1.body.decode()
        assert "9 1 1" in body1  # advisory spoken immediately

        r2 = await _gather("CA-INJURY", "anyway the door is smashed in")
        assert "9 1 1" not in r2.body.decode()  # advisory only once

        stored = repo.load_session_by_call("CA-INJURY")
        fields = stored.channel_metadata["scripted_intake"]["fields"]
        assert fields["injury_mentions"]


def test_injury_advisory_text_gives_no_medical_advice_beyond_911():
    lowered = INJURY_SAFETY_ADVISORY.lower()
    assert "9 1 1" in lowered
    for forbidden in ("diagnos", "medication", "dosage", "treat", "symptom"):
        assert forbidden not in lowered


# ---------------------------------------------------------------------------
# Narrative state machine unit coverage
# ---------------------------------------------------------------------------


def test_narrative_segment_cap_completes():
    from src.orchestrator.schemas import OrchestratorSession

    session = OrchestratorSession(session_id="s1", call_sid="CA-CAP")
    workflow = BirchwoodCollisionIntakeWorkflow()
    stage = next(
        s
        for s in workflow.get_scripted_intake_definition().stages
        if s.field_name == "incident_description"
    )
    outcome = None
    for i in range(stability.NARRATIVE_MAX_SEGMENTS + 1):
        outcome = stability.narrative_ingest(session, stage, f"segment number {i}")
        if outcome.completed:
            break
    assert outcome is not None and outcome.completed
    assert "segment number 0" in outcome.joined_text


def test_narrative_char_cap_completes():
    from src.orchestrator.schemas import OrchestratorSession

    session = OrchestratorSession(session_id="s2", call_sid="CA-CHARS")
    workflow = BirchwoodCollisionIntakeWorkflow()
    stage = next(
        s
        for s in workflow.get_scripted_intake_definition().stages
        if s.field_name == "incident_description"
    )
    # A single utterance already over the cap completes immediately —
    # capture everything said, but do not keep the call open indefinitely.
    big = "x" * (stability.NARRATIVE_MAX_CHARS + 10)
    outcome = stability.narrative_ingest(session, stage, big)
    assert outcome.completed
    assert big in outcome.joined_text


@pytest.mark.parametrize(
    "cue",
    ["that's everything", "that's all", "nothing else", "I'm done", "no"],
)
def test_completion_cues_recognized(cue):
    from src.orchestrator.schemas import OrchestratorSession

    session = OrchestratorSession(session_id="s3", call_sid="CA-CUE")
    workflow = BirchwoodCollisionIntakeWorkflow()
    stage = next(
        s
        for s in workflow.get_scripted_intake_definition().stages
        if s.field_name == "incident_description"
    )
    stability.narrative_ingest(session, stage, "the bumper is cracked")
    outcome = stability.narrative_ingest(session, stage, cue)
    assert outcome.completed
    assert outcome.joined_text == "the bumper is cracked"
