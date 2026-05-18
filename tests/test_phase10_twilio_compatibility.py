from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from src.platform.workflows.schemas import ResolvedWorkflowRoute, WorkflowTurnResult


def test_twilio_incoming_invokes_route_resolution_and_returns_twiml():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import reset_session_repository

    resolver = MagicMock()
    resolver.resolve.return_value = ResolvedWorkflowRoute(
        vertical_key="healthcare",
        workflow_id="healthcare_triage_v1",
        workflow_version="v1",
        fallback_used=True,
        fallback_reason="test",
    )

    with (
        patch("src.config.STORAGE_BACKEND", "memory"),
        patch("src.config.ENVIRONMENT", "development"),
        patch("src.config.DATABASE_URL", None),
        patch("src.security.twilio_signature.TWILIO_VALIDATE_SIGNATURE", False),
        patch("src.twilio.routes.get_workflow_route_resolver", return_value=resolver),
        patch("src.twilio.routes.text_to_speech_url", new=AsyncMock(return_value=None)),
    ):
        reset_session_repository()
        reset_storage_backend()
        from src.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/voice/incoming",
            data={
                "CallSid": "CA-PHASE10",
                "From": "+15557654321",
                "To": "+15551234567",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Response>" in response.text
    assert "Gather" in response.text
    resolver.resolve.assert_called_once_with(called_phone_number="+15551234567")


@pytest.mark.asyncio
async def test_twilio_shared_number_menu_routes_digit_two_to_insurance():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes
    from src.verticals.insurance.constants import INSURANCE_CLAIMS_FNOL_WORKFLOW_ID

    resolver = MagicMock()
    resolver.resolve.return_value = ResolvedWorkflowRoute(
        vertical_key="healthcare",
        workflow_id="healthcare_triage_v1",
        workflow_version="v1",
        fallback_used=False,
    )

    with (
        patch("src.config.STORAGE_BACKEND", "memory"),
        patch("src.config.ENVIRONMENT", "development"),
        patch("src.config.DATABASE_URL", None),
        patch("src.config.ENABLE_SHARED_NUMBER_VERTICAL_MENU", True),
        patch("src.config.SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER", "+15551234567"),
        patch("src.twilio.routes.get_workflow_route_resolver", return_value=resolver),
        patch("src.twilio.routes.text_to_speech_url", new=AsyncMock(return_value=None)),
    ):
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()

        response = await twilio_routes.handle_incoming_call(
            SimpleNamespace(headers={}),
            CallSid="CA-SHARED-MENU",
            To="+15551234567",
        )

        body = response.body.decode()
        assert "insurance claims" in body
        session = repo.load_session_by_call("CA-SHARED-MENU")
        assert session.channel_metadata["stage"] == twilio_routes.STAGE_VERTICAL_MENU

        response = await twilio_routes.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-SHARED-MENU",
            To="+15551234567",
            Digits="2",
        )

    body = response.body.decode()
    stored = repo.load_session_by_call("CA-SHARED-MENU")
    assert "insurance claims intake line" in body
    assert "Can I get your name" in body
    assert stored.vertical_key == "insurance"
    assert stored.workflow_id == INSURANCE_CLAIMS_FNOL_WORKFLOW_ID
    assert stored.channel_metadata["stage"] == "CALLER_NAME"


@pytest.mark.asyncio
async def test_twilio_shared_number_menu_routes_digit_three_to_automotive():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes
    from src.verticals.automotive_collision.constants import (
        BIRCHWOOD_COLLISION_WORKFLOW_ID,
    )

    resolver = MagicMock()
    resolver.resolve.return_value = ResolvedWorkflowRoute(
        vertical_key="healthcare",
        workflow_id="healthcare_triage_v1",
        workflow_version="v1",
        fallback_used=False,
    )

    with (
        patch("src.config.STORAGE_BACKEND", "memory"),
        patch("src.config.ENVIRONMENT", "development"),
        patch("src.config.DATABASE_URL", None),
        patch("src.config.ENABLE_SHARED_NUMBER_VERTICAL_MENU", True),
        patch("src.config.SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER", "+15551234567"),
        patch("src.twilio.routes.get_workflow_route_resolver", return_value=resolver),
        patch("src.twilio.routes.text_to_speech_url", new=AsyncMock(return_value=None)),
    ):
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()

        response = await twilio_routes.handle_incoming_call(
            SimpleNamespace(headers={}),
            CallSid="CA-SHARED-MENU-AUTO",
            To="+15551234567",
        )

        body = response.body.decode()
        assert "insurance claims" in body
        session = repo.load_session_by_call("CA-SHARED-MENU-AUTO")
        assert session.channel_metadata["stage"] == twilio_routes.STAGE_VERTICAL_MENU

        response = await twilio_routes.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-SHARED-MENU-AUTO",
            To="+15551234567",
            Digits="3",
        )

    body = response.body.decode()
    stored = repo.load_session_by_call("CA-SHARED-MENU-AUTO")
    assert "Birchwood Collision" in body
    assert "safe to drive right now" in body.lower()
    assert stored.vertical_key == "automotive_collision"
    assert stored.workflow_id == BIRCHWOOD_COLLISION_WORKFLOW_ID
    assert stored.channel_metadata["stage"] == "DRIVABILITY_CHECK"


def test_shared_number_menu_prefers_insurance_for_auto_claim_language():
    from src.twilio import routes as twilio_routes

    assert twilio_routes._parse_vertical_menu_choice("auto claim", None) == "insurance"
    assert (
        twilio_routes._parse_vertical_menu_choice("car insurance claim", None)
        == "insurance"
    )


@pytest.mark.asyncio
async def test_twilio_scripted_intake_preserved_and_dynamic_uses_workflow_engine():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes

    with (
        patch("src.config.STORAGE_BACKEND", "memory"),
        patch("src.config.ENVIRONMENT", "development"),
        patch("src.config.DATABASE_URL", None),
        patch("src.twilio.routes.text_to_speech_url", new=AsyncMock(return_value=None)),
    ):
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-SCRIPT",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key="healthcare",
                workflow_id="healthcare_triage_v1",
                workflow_version="v1",
            ),
        )
        session.channel_metadata["stage"] = twilio_routes.STAGE_NAME
        repo.persist_session(session)

        request = SimpleNamespace(headers={})

        response = await twilio_routes.handle_gather(
            request,
            BackgroundTasks(),
            CallSid="CA-SCRIPT",
            SpeechResult="Jane Doe",
        )
        assert "What is your age" in response.body.decode()
        assert (
            repo.load_session_by_call("CA-SCRIPT").intake_state.caller_name
            == "Jane Doe"
        )

        response = await twilio_routes.handle_gather(
            request,
            BackgroundTasks(),
            CallSid="CA-SCRIPT",
            SpeechResult="42",
        )
        assert "biological sex" in response.body.decode()
        assert repo.load_session_by_call("CA-SCRIPT").intake_state.caller_age == 42

        response = await twilio_routes.handle_gather(
            request,
            BackgroundTasks(),
            CallSid="CA-SCRIPT",
            SpeechResult="male",
        )
        assert "what brings you in today" in response.body.decode().lower()
        assert repo.load_session_by_call("CA-SCRIPT").intake_state.caller_sex == "male"

        engine = MagicMock()

        async def _handle_turn(context, workflow_input):
            updated = repo.load_session_by_call("CA-SCRIPT")
            updated.channel_metadata["stage"] = twilio_routes.STAGE_DYNAMIC
            return WorkflowTurnResult(
                assistant_text="When did this start?",
                stage="DYNAMIC",
                should_continue=True,
                should_finalize=False,
                escalation_required=False,
                updated_state=updated.model_dump(mode="json"),
                audit_metadata={"test": True},
            )

        engine.handle_turn = AsyncMock(side_effect=_handle_turn)
        with patch("src.twilio.routes.get_workflow_engine", return_value=engine):
            response = await twilio_routes.handle_gather(
                request,
                BackgroundTasks(),
                CallSid="CA-SCRIPT",
                SpeechResult="I have a headache",
            )

            assert "/api/v1/voice/thinking" in response.body.decode()
            pending = twilio_routes._pending_turns.pop("CA-SCRIPT")
            task, _session_obj = pending
            legacy_result, updated_session = await task

        assert engine.handle_turn.await_count == 1
        assert legacy_result["action"] == "ask"
        assert updated_session.intake_state.chief_complaint == "I have a headache"
