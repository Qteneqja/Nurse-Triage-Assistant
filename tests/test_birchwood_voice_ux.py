from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks

from src.orchestrator.schemas import OrchestratorSession
from src.platform.workflows.schemas import ResolvedWorkflowRoute
from src.verticals.automotive_collision.workflow import (
    BirchwoodCollisionIntakeWorkflow,
)
from src.verticals.automotive_collision.constants import (
    AUTOMOTIVE_COLLISION_VERTICAL,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.healthcare.workflow import HealthcareTriageWorkflow
from src.verticals.insurance.workflow import InsuranceClaimsFnolWorkflow


def _stage_by_field(workflow, field_name: str):
    intake = workflow.get_scripted_intake_definition()
    return next(stage for stage in intake.stages if stage.field_name == field_name)


def test_birchwood_intro_and_narrative_prompt_are_warm_and_non_clinical():
    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    incident_stage = _stage_by_field(
        BirchwoodCollisionIntakeWorkflow(), "incident_description"
    )

    intro = (intake.intro_text or "").lower()
    prompt = (incident_stage.prompt_text or incident_stage.prompt).lower()

    # Voice pass: Birchwood Automotive Group service-center greeting with
    # the recording notice, pivoting straight into warm intake.
    assert "thank you for calling birchwood automotive group" in intro
    assert "recorded for training and quality purposes" in intro
    assert "press 0" in intro
    assert "next available representative" not in intro  # we ARE the intake
    assert "take your time" in prompt
    assert "what happened" in prompt
    for forbidden in ["patient", "symptom", "symptoms", "nurse", "clinical", "sbar"]:
        assert forbidden not in intro
        assert forbidden not in prompt


def test_birchwood_incident_description_uses_narrative_profile_defaults():
    stage = _stage_by_field(BirchwoodCollisionIntakeWorkflow(), "incident_description")

    assert stage.timeout_seconds == 15
    assert stage.speech_timeout == "auto"
    assert stage.speech_profile == "narrative"


def test_birchwood_short_fields_keep_short_default_profile():
    vehicle_year_stage = _stage_by_field(
        BirchwoodCollisionIntakeWorkflow(), "vehicle_year"
    )
    drivable_stage = _stage_by_field(BirchwoodCollisionIntakeWorkflow(), "is_drivable")

    assert vehicle_year_stage.timeout_seconds == 5
    assert vehicle_year_stage.speech_timeout == "3"
    assert vehicle_year_stage.speech_profile == "short_field"
    assert drivable_stage.timeout_seconds == 5
    assert drivable_stage.speech_timeout == "3"
    assert drivable_stage.speech_profile == "short_field"


def test_healthcare_and_insurance_speech_settings_are_unchanged():
    healthcare_stage = _stage_by_field(HealthcareTriageWorkflow(), "chief_complaint")
    insurance_stage = _stage_by_field(
        InsuranceClaimsFnolWorkflow(),
        "incident_summary",
    )

    assert healthcare_stage.timeout_seconds == 10
    assert healthcare_stage.speech_timeout == "auto"
    assert healthcare_stage.speech_profile is None
    assert insurance_stage.timeout_seconds == 10
    assert insurance_stage.speech_timeout == "auto"
    assert insurance_stage.speech_profile is None


@pytest.mark.asyncio
async def test_birchwood_prompting_records_narrative_prompt_metadata():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes

    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    incident_stage = next(
        stage for stage in intake.stages if stage.field_name == "incident_description"
    )

    with (
        pytest.MonkeyPatch.context() as mp,
        patch(
            "src.twilio.routes.text_to_speech_url",
            new=AsyncMock(return_value=None),
        ),
    ):
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-BIRCHWOOD-NARRATIVE-PROMPT",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
                workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
                workflow_version="v1",
            ),
        )
        # PR 2: the narrative is the FIRST stage — its prompt and metadata
        # come from the initial scripted TwiML at call start.
        twiml = await twilio_routes._generate_initial_scripted_twiml(
            session,
            intake.intro_text,
            incident_stage,
            "/api/v1/voice/gather",
        )
        repo.persist_session(session)

    stored = repo.load_session_by_call("CA-BIRCHWOOD-NARRATIVE-PROMPT")
    body = twiml.lower()
    prompt_metadata = stored.channel_metadata["scripted_intake"]["last_prompt_metadata"]

    assert "take your time" in body
    assert prompt_metadata["workflow_id"] == BIRCHWOOD_COLLISION_WORKFLOW_ID
    assert prompt_metadata["field_name"] == "incident_description"
    assert prompt_metadata["speech_profile"] == "narrative"
    assert prompt_metadata["timeout_seconds"] == 15
    assert prompt_metadata["speech_timeout"] == "auto"


@pytest.mark.asyncio
async def test_birchwood_narrative_response_adds_brief_ack_before_next_question():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes

    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    incident_stage = next(
        stage for stage in intake.stages if stage.field_name == "incident_description"
    )

    with (
        pytest.MonkeyPatch.context() as mp,
        patch(
            "src.twilio.routes.text_to_speech_url",
            new=AsyncMock(return_value=None),
        ),
    ):
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-BIRCHWOOD-NARRATIVE-ACK",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
                workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
                workflow_version="v1",
            ),
        )
        session.channel_metadata["stage"] = incident_stage.stage_id
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": intake.stages.index(incident_stage),
            "current_stage_id": incident_stage.stage_id,
            "fields": {},
            "attempts": {},
            "completed": False,
        }
        repo.persist_session(session)

        # First narrative segment: the assistant keeps listening instead of
        # cutting the caller off (PR 1 multi-segment narrative capture).
        response = await twilio_routes.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-BIRCHWOOD-NARRATIVE-ACK",
            SpeechResult="Someone hit the rear quarter panel while I was parked.",
        )
        first_body = response.body.decode().lower()
        assert "go on, i'm listening" in first_body
        assert "was anyone hurt" not in first_body

        # Completion cue: brief ack, then the next scripted question.
        response = await twilio_routes.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-BIRCHWOOD-NARRATIVE-ACK",
            SpeechResult="that's everything",
        )

        stored = repo.load_session_by_call("CA-BIRCHWOOD-NARRATIVE-ACK")
        fields = stored.channel_metadata["scripted_intake"]["fields"]
        assert (
            fields["incident_description"]
            == "Someone hit the rear quarter panel while I was parked."
        )

    body = response.body.decode().lower()
    assert "got it. i noted that." in body
    # PR 2: the injury check (Invariant 3) is the first gap-fill question.
    assert "was anyone hurt" in body


@pytest.mark.asyncio
async def test_birchwood_press_zero_during_scripted_intake_transfers_immediately():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes

    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    drivable_stage = next(
        stage for stage in intake.stages if stage.field_name == "is_drivable"
    )

    with (
        pytest.MonkeyPatch.context() as mp,
        patch(
            "src.twilio.routes.text_to_speech_url",
            new=AsyncMock(return_value=None),
        ),
    ):
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-BIRCHWOOD-PRESS-ZERO",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
                workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
                workflow_version="v1",
            ),
        )
        session.channel_metadata["stage"] = drivable_stage.stage_id
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": intake.stages.index(drivable_stage),
            "current_stage_id": drivable_stage.stage_id,
            "fields": {},
            "attempts": {},
            "completed": False,
        }
        repo.persist_session(session)

        response = await twilio_routes.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-BIRCHWOOD-PRESS-ZERO",
            Digits="0",
        )

    body = response.body.decode().lower()
    stored = repo.load_session_by_call("CA-BIRCHWOOD-PRESS-ZERO")
    assert "straight through to our collision team" in body
    assert stored.is_finalized is True
    assert stored.channel_metadata["workflow_final_result"]["final_disposition"] == (
        "TRANSFER_COLLISION_CENTER"
    )


@pytest.mark.asyncio
async def test_birchwood_spoken_transfer_during_scripted_intake_transfers_immediately():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes

    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    drivable_stage = next(
        stage for stage in intake.stages if stage.field_name == "is_drivable"
    )

    with (
        pytest.MonkeyPatch.context() as mp,
        patch(
            "src.twilio.routes.text_to_speech_url",
            new=AsyncMock(return_value=None),
        ),
    ):
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-BIRCHWOOD-SPEECH-TRANSFER",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
                workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
                workflow_version="v1",
            ),
        )
        session.channel_metadata["stage"] = drivable_stage.stage_id
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": intake.stages.index(drivable_stage),
            "current_stage_id": drivable_stage.stage_id,
            "fields": {},
            "attempts": {},
            "completed": False,
        }
        repo.persist_session(session)

        response = await twilio_routes.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-BIRCHWOOD-SPEECH-TRANSFER",
            SpeechResult="transfer me please",
        )

    body = response.body.decode().lower()
    stored = repo.load_session_by_call("CA-BIRCHWOOD-SPEECH-TRANSFER")
    assert "straight through to our collision team" in body
    assert stored.is_finalized is True


@pytest.mark.asyncio
async def test_birchwood_scripted_gather_allows_press_zero_dtmf():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes

    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    drivable_stage = next(
        stage for stage in intake.stages if stage.field_name == "is_drivable"
    )

    with (
        pytest.MonkeyPatch.context() as mp,
        patch(
            "src.twilio.routes.text_to_speech_url",
            new=AsyncMock(return_value=None),
        ),
    ):
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-BIRCHWOOD-GATHER-DTMF",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
                workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
                workflow_version="v1",
            ),
        )
        session.channel_metadata["stage"] = drivable_stage.stage_id
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": intake.stages.index(drivable_stage),
            "current_stage_id": drivable_stage.stage_id,
            "fields": {},
            "attempts": {},
            "completed": False,
        }

        twiml = await twilio_routes.generate_twiml_gather(
            twilio_routes._stage_prompt(drivable_stage),
            "/api/v1/voice/gather",
            timeout=drivable_stage.timeout_seconds,
            hints=drivable_stage.hints,
            speech_timeout=drivable_stage.speech_timeout,
            session=session,
        )

    assert 'input="speech dtmf"' in twiml
    assert 'numDigits="1"' in twiml


@pytest.mark.asyncio
async def test_birchwood_initial_scripted_prompt_allows_press_zero_dtmf():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes

    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    first_stage = intake.stages[0]

    with (
        pytest.MonkeyPatch.context() as mp,
        patch(
            "src.twilio.routes.text_to_speech_url",
            new=AsyncMock(return_value=None),
        ),
    ):
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-BIRCHWOOD-INITIAL-GATHER-DTMF",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
                workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
                workflow_version="v1",
            ),
        )
        session.channel_metadata["stage"] = first_stage.stage_id
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": 0,
            "current_stage_id": first_stage.stage_id,
            "fields": {},
            "attempts": {},
            "completed": False,
        }

        twiml = await twilio_routes._generate_initial_scripted_twiml(
            session,
            "Thanks for calling Birchwood.",
            first_stage,
            "/api/v1/voice/gather",
        )

    assert 'input="speech dtmf"' in twiml
    assert 'numDigits="1"' in twiml


@pytest.mark.asyncio
async def test_birchwood_empty_speech_retry_keeps_birchwood_tts_profile():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes

    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    incident_stage = next(
        stage for stage in intake.stages if stage.field_name == "incident_description"
    )

    with (
        pytest.MonkeyPatch.context() as mp,
        patch(
            "src.twilio.routes.text_to_speech_url",
            new=AsyncMock(return_value=None),
        ) as tts_mock,
    ):
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        mp.setattr(
            "src.config.BIRCHWOOD_AZURE_TTS_VOICE",
            "en-US-Bree:DragonHDLatestNeural",
        )
        mp.setattr("src.config.BIRCHWOOD_AZURE_TTS_STYLE", "friendly")
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-BIRCHWOOD-EMPTY-RETRY",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
                workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
                workflow_version="v1",
            ),
        )
        session.channel_metadata["stage"] = incident_stage.stage_id
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": intake.stages.index(incident_stage),
            "current_stage_id": incident_stage.stage_id,
            "fields": {},
            "attempts": {},
            "completed": False,
        }
        repo.persist_session(session)

        response = await twilio_routes.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-BIRCHWOOD-EMPTY-RETRY",
            SpeechResult="",
        )

    body = response.body.decode()
    args = tts_mock.await_args.args
    kwargs = tts_mock.await_args.kwargs
    assert 'timeout="15"' in body
    assert 'speechTimeout="auto"' in body
    assert args[1] == "en-US-Bree:DragonHDLatestNeural"
    assert kwargs["style"] == "friendly"


@pytest.mark.asyncio
async def test_birchwood_validation_reprompt_keeps_birchwood_tts_profile():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes

    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    caller_name_stage = next(
        stage for stage in intake.stages if stage.field_name == "caller_name"
    )

    with (
        pytest.MonkeyPatch.context() as mp,
        patch(
            "src.twilio.routes.text_to_speech_url",
            new=AsyncMock(return_value=None),
        ) as tts_mock,
    ):
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        mp.setattr(
            "src.config.BIRCHWOOD_AZURE_TTS_VOICE",
            "en-US-Bree:DragonHDLatestNeural",
        )
        mp.setattr("src.config.BIRCHWOOD_AZURE_TTS_STYLE", "friendly")
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-BIRCHWOOD-VALIDATION-REPROMPT",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
                workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
                workflow_version="v1",
            ),
        )
        session.channel_metadata["stage"] = caller_name_stage.stage_id
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
            "current_index": intake.stages.index(caller_name_stage),
            "current_stage_id": caller_name_stage.stage_id,
            "fields": {},
            "attempts": {},
            "completed": False,
        }
        repo.persist_session(session)

        response = await twilio_routes.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-BIRCHWOOD-VALIDATION-REPROMPT",
            SpeechResult="hello",
        )

    body = response.body.decode().lower()
    args = tts_mock.await_args.args
    kwargs = tts_mock.await_args.kwargs
    assert "first and last name" in body
    assert args[1] == "en-US-Bree:DragonHDLatestNeural"
    assert kwargs["style"] == "friendly"


def test_birchwood_tts_settings_default_to_bree_dragonhd_voice(monkeypatch):
    from src.twilio import routes as twilio_routes

    session = OrchestratorSession(
        session_id="birchwood-tts",
        workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
    )
    monkeypatch.setattr(
        "src.config.AZURE_TTS_VOICE",
        "en-US-Bree:DragonHDLatestNeural",
        raising=False,
    )
    monkeypatch.setattr(
        "src.config.BIRCHWOOD_AZURE_TTS_VOICE",
        "en-US-Bree:DragonHDLatestNeural",
        raising=False,
    )
    monkeypatch.setattr("src.config.BIRCHWOOD_AZURE_TTS_RATE", "+3%", raising=False)
    monkeypatch.setattr("src.config.BIRCHWOOD_AZURE_TTS_PITCH", "+0%", raising=False)
    monkeypatch.setattr(
        "src.config.BIRCHWOOD_AZURE_TTS_STYLE",
        "",
        raising=False,
    )
    monkeypatch.setattr("src.config.BIRCHWOOD_AZURE_TTS_BREAK_MS", 250, raising=False)

    settings = twilio_routes._get_tts_settings_for_session(session)

    assert settings["voice"] == "en-US-Bree:DragonHDLatestNeural"
    assert settings["rate"] == "+3%"
    assert settings["pitch"] == "+0%"
    assert settings["style"] is None
    assert settings["break_ms"] == 250
    assert settings["fallback_voice"] == "en-US-Bree:DragonHDLatestNeural"


@pytest.mark.parametrize("style_value", ["", "none", "default", "plain"])
def test_birchwood_tts_settings_treat_plain_style_markers_as_unstyled(
    monkeypatch, style_value
):
    from src.twilio import routes as twilio_routes

    session = OrchestratorSession(
        session_id="birchwood-tts-plain-style",
        workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
    )
    monkeypatch.setattr(
        "src.config.BIRCHWOOD_AZURE_TTS_STYLE",
        style_value,
        raising=False,
    )

    settings = twilio_routes._get_tts_settings_for_session(session)

    assert settings["style"] is None


def test_birchwood_tts_settings_fall_back_to_global_voice_when_blank(monkeypatch):
    from src.twilio import routes as twilio_routes

    session = OrchestratorSession(
        session_id="birchwood-tts-fallback",
        workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
    )
    monkeypatch.setattr(
        "src.config.AZURE_TTS_VOICE",
        "en-US-Bree:DragonHDLatestNeural",
        raising=False,
    )
    monkeypatch.setattr(
        "src.config.BIRCHWOOD_AZURE_TTS_VOICE",
        "",
        raising=False,
    )

    settings = twilio_routes._get_tts_settings_for_session(session)

    assert settings["voice"] == "en-US-Bree:DragonHDLatestNeural"


class _FakeAzureResponse:
    def __init__(self, status_code: int, *, content: bytes = b"", text: str = ""):
        self.status_code = status_code
        self.content = content
        self.text = text


class _FakeAzureClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def post(self, url, content=None, headers=None):
        self.calls.append({"url": url, "content": content, "headers": headers})
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_azure_tts_retries_without_style_when_style_is_unsupported(monkeypatch):
    from src.utils import azure_tts

    fake_client = _FakeAzureClient(
        [
            _FakeAzureResponse(400, text="Style is not supported for this voice"),
            _FakeAzureResponse(200, content=b"birchwood-audio"),
        ]
    )

    monkeypatch.setattr("src.config.AZURE_SPEECH_KEY", "test-key", raising=False)
    monkeypatch.setattr("src.config.AZURE_SPEECH_REGION", "eastus", raising=False)
    monkeypatch.setattr(azure_tts, "_get_client", lambda: fake_client)

    audio = await azure_tts.synthesize_speech(
        "Hello from Birchwood.",
        voice="en-US-Bree:DragonHDLatestNeural",
        style="friendly",
    )

    assert audio == b"birchwood-audio"
    assert len(fake_client.calls) == 2
    assert "mstts:express-as" in fake_client.calls[0]["content"]
    assert "mstts:express-as" not in fake_client.calls[1]["content"]
