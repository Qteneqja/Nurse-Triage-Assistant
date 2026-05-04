from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from unittest.mock import AsyncMock, MagicMock, patch

from src.platform.workflows.schemas import ResolvedWorkflowRoute, WorkflowTurnResult
from src.verticals.property_management.constants import (
    PROPERTY_MAINTENANCE_WORKFLOW_ID,
)


@pytest.mark.asyncio
async def test_healthcare_scripted_intake_uses_workflow_definition():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes
    from src.verticals.healthcare.workflow import HealthcareTriageWorkflow

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-HC-GENERIC",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key="healthcare",
                workflow_id="healthcare_triage_v1",
                workflow_version="v1",
            ),
        )
        intake = HealthcareTriageWorkflow().get_scripted_intake_definition()
        twilio_routes._initialize_scripted_intake(session, intake)
        repo.persist_session(session)

        response = await twilio_routes.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-HC-GENERIC",
            SpeechResult="Jane Doe",
        )

    stored = repo.load_session_by_call("CA-HC-GENERIC")
    assert "What is your age" in response.body.decode()
    assert stored.channel_metadata["scripted_intake"]["fields"]["caller_name"] == (
        "Jane Doe"
    )
    assert stored.intake_state.caller_name == "Jane Doe"
    assert stored.channel_metadata["stage"] == "AGE"


@pytest.mark.asyncio
async def test_healthcare_prefer_not_to_say_normalizes_to_schema_safe_unknown():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes
    from src.verticals.healthcare.workflow import HealthcareTriageWorkflow

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-HC-SEX-UNKNOWN",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key="healthcare",
                workflow_id="healthcare_triage_v1",
                workflow_version="v1",
            ),
        )
        intake = HealthcareTriageWorkflow().get_scripted_intake_definition()
        twilio_routes._initialize_scripted_intake(session, intake)
        repo.persist_session(session)

        for speech in ["Jane Doe", "42", "prefer not to say"]:
            await twilio_routes.handle_gather(
                SimpleNamespace(headers={}),
                BackgroundTasks(),
                CallSid="CA-HC-SEX-UNKNOWN",
                SpeechResult=speech,
            )

        engine = MagicMock()

        async def _handle_turn(context, workflow_input):
            updated = repo.load_session_by_call("CA-HC-SEX-UNKNOWN")
            assert updated.intake_state.caller_sex == "unknown"
            return WorkflowTurnResult(
                assistant_text="When did this start?",
                stage="DYNAMIC",
                should_continue=True,
                should_finalize=False,
                escalation_required=False,
                updated_state=updated.model_dump(mode="json"),
            )

        engine.handle_turn = AsyncMock(side_effect=_handle_turn)
        with patch("src.twilio.routes.get_workflow_engine", return_value=engine):
            response = await twilio_routes.handle_gather(
                SimpleNamespace(headers={}),
                BackgroundTasks(),
                CallSid="CA-HC-SEX-UNKNOWN",
                SpeechResult="I have a headache",
            )
            pending = twilio_routes._pending_turns.pop("CA-HC-SEX-UNKNOWN")
            task, _session_obj = pending
            legacy_result, updated_session = await task

    assert "/api/v1/voice/thinking" in response.body.decode()
    assert legacy_result["action"] == "ask"
    assert updated_session.intake_state.caller_sex == "unknown"
    assert engine.handle_turn.await_count == 1


@pytest.mark.asyncio
async def test_property_scripted_intake_collects_fields_and_hands_to_workflow():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes
    from src.verticals.property_management.workflow import (
        PropertyManagementMaintenanceWorkflow,
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.config.STORAGE_BACKEND", "memory")
        mp.setattr("src.config.ENVIRONMENT", "development")
        mp.setattr("src.config.DATABASE_URL", None)
        reset_session_repository()
        reset_storage_backend()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-PROP-GENERIC",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key="property_management",
                workflow_id=PROPERTY_MAINTENANCE_WORKFLOW_ID,
                workflow_version="v1",
            ),
        )
        intake = (
            PropertyManagementMaintenanceWorkflow().get_scripted_intake_definition()
        )
        twilio_routes._initialize_scripted_intake(session, intake)
        repo.persist_session(session)

        answers = [
            ("Jordan Smith", "property address"),
            ("123 Main Street", "unit or apartment"),
            ("4B", "What type of issue"),
            ("plumbing", "briefly describe"),
            ("active flooding from a burst pipe", "permission to enter"),
            ("yes", "best phone number"),
        ]
        for speech, expected_next_prompt in answers:
            response = await twilio_routes.handle_gather(
                SimpleNamespace(headers={}),
                BackgroundTasks(),
                CallSid="CA-PROP-GENERIC",
                SpeechResult=speech,
            )
            assert expected_next_prompt in response.body.decode()

        response = await twilio_routes.handle_gather(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid="CA-PROP-GENERIC",
            SpeechResult="555 123 4567",
        )

        assert "/api/v1/voice/thinking" in response.body.decode()
        pending = twilio_routes._pending_turns.pop("CA-PROP-GENERIC")
        task, _session_obj = pending
        legacy_result, updated_session = await task

    fields = updated_session.channel_metadata["scripted_intake"]["fields"]
    assert fields["property_address"] == "123 Main Street"
    assert fields["callback_phone"] == "+15551234567"
    assert legacy_result["action"] == "escalate"
    final_result = updated_session.channel_metadata["workflow_final_result"]
    assert final_result["final_disposition"] == "EMERGENCY"
    assert final_result["structured_output"]["work_order"]["vendor_type"] == "plumbing"
