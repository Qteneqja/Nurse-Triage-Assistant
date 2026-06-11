"""PR 3 — Workflow engine: spec-defined workflows, hard-wired safety.

Locks the engine contract:
  - a toy workflow is added with ONE definition file + config only and runs
    end-to-end through the real voice channel
  - a malicious/misconfigured definition cannot bypass the injury safety
    branch or touch the healthcare stack (reserved ids/verticals)
  - generic phone routing comes from WORKFLOW_PHONE_ROUTES config
  - Birchwood exposes a complete WorkflowSpec while behaving exactly as
    before (the rest of the suite proves equivalence)
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks

from src.platform.workflows.registry import (
    ensure_default_workflows_registered,
    reset_workflow_registry,
)
from src.platform.workflows.schemas import (
    WorkflowContext,
    WorkflowTurnResult,
)
from src.platform.workflows.spec import (
    RESERVED_WORKFLOW_IDS,
    WorkflowSpec,
)
from src.platform.workflows.spec_loader import register_spec_definitions
from src.platform.workflows.spec_workflow import SpecDrivenWorkflow, safe_format

TOY_SPEC = {
    "workflow_id": "towing_followup_v1",
    "vertical": "roadside",
    "display_name": "Towing Follow-up",
    "greeting": "Thanks for calling the towing follow-up line.",
    "stages": [
        {
            "stage_id": "REASON",
            "field_name": "reason",
            "prompt": "What do you need help with today?",
        },
        {
            "stage_id": "CALLER_NAME",
            "field_name": "caller_name",
            "prompt": "What's your full name?",
        },
        {
            "stage_id": "PHONE",
            "field_name": "phone",
            "prompt": "Best callback number?",
            "field_type": "phone",
        },
    ],
    "required_fields": ["reason", "caller_name", "phone"],
    "dashboard_display_fields": ["caller_name", "phone", "reason"],
    "recommended_actions": {
        "COMPLETED_INTAKE": "Call the customer back about their tow."
    },
    "summary_templates": {
        "caller": "Thanks {caller_name}, the team will call you back.",
        "business": "{outcome} for {caller_name}.",
    },
}

_TTS_OFF = patch(
    "src.twilio.routes.text_to_speech_url",
    new=AsyncMock(return_value=None),
)


def _setup(mp: pytest.MonkeyPatch, tmp_path: Path | None = None):
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
    if tmp_path is not None:
        mp.setattr("src.config.EXTRA_WORKFLOW_DEFINITIONS_DIR", str(tmp_path))
    reset_session_repository()
    reset_storage_backend()
    reset_workflow_route_resolver()
    reset_workflow_registry()
    return get_session_repository()


async def _gather(call_sid: str, speech: str | None):
    from src.twilio import routes as twilio_routes

    return await twilio_routes.handle_gather(
        SimpleNamespace(headers={}),
        BackgroundTasks(),
        CallSid=call_sid,
        SpeechResult=speech,
    )


# ---------------------------------------------------------------------------
# Toy workflow: ONE definition file + config only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toy_workflow_from_definition_file_runs_end_to_end(tmp_path):
    from src.twilio import routes as twilio_routes

    (tmp_path / "towing_followup_v1.json").write_text(
        json.dumps(TOY_SPEC), encoding="utf-8"
    )
    call_sid = "CA-TOY-CLEAN"
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup(mp, tmp_path)
        mp.setattr(
            "src.config.WORKFLOW_PHONE_ROUTES",
            {"+15555550150": "towing_followup_v1"},
        )

        response = await twilio_routes.handle_incoming_call(
            SimpleNamespace(headers={}),
            CallSid=call_sid,
            To="+15555550150",
        )
        body = response.body.decode()
        assert "towing follow-up line" in body
        assert "What do you need help with today" in body

        await _gather(call_sid, "my car got towed last night and I need an update")
        await _gather(call_sid, "Tony Tester")
        await _gather(call_sid, "204 555 0166")

        task, _ = twilio_routes._pending_turns[call_sid]
        await task
        final = await twilio_routes.handle_thinking(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid=call_sid,
        )
        closing = final.body.decode()
        assert "Thanks Tony Tester" in closing  # caller summary template

        stored = repo.load_session_by_call(call_sid)
        record = stored.channel_metadata["workflow_final_result"]["structured_output"][
            "intake_record"
        ]
        assert record["outcome"] == "COMPLETED_INTAKE"
        assert record["dashboard"] == {
            "caller_name": "Tony Tester",
            "phone": "+12045550166",
            "reason": "my car got towed last night and I need an update",
        }
        assert record["recommended_action"] == (
            "Call the customer back about their tow."
        )


@pytest.mark.asyncio
async def test_toy_workflow_injury_mention_cannot_bypass_safety(tmp_path):
    """The toy spec declares NO safety handling at all — the platform layers
    must still advise 9-1-1 (exactly once) and flag the record."""
    from src.twilio import routes as twilio_routes

    (tmp_path / "towing_followup_v1.json").write_text(
        json.dumps(TOY_SPEC), encoding="utf-8"
    )
    call_sid = "CA-TOY-INJURY"
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup(mp, tmp_path)
        mp.setattr(
            "src.config.WORKFLOW_PHONE_ROUTES",
            {"+15555550150": "towing_followup_v1"},
        )
        await twilio_routes.handle_incoming_call(
            SimpleNamespace(headers={}),
            CallSid=call_sid,
            To="+15555550150",
        )
        spoken = []
        r = await _gather(
            call_sid, "my car was towed after a crash and my wrist is bleeding"
        )
        spoken.append(r.body.decode())
        assert "9 1 1" in spoken[-1]  # advisory at first mention

        spoken.append((await _gather(call_sid, "Pat Hurt")).body.decode())
        spoken.append((await _gather(call_sid, "204 555 0177")).body.decode())

        task, _ = twilio_routes._pending_turns[call_sid]
        await task
        final = await twilio_routes.handle_thinking(
            SimpleNamespace(headers={}),
            BackgroundTasks(),
            CallSid=call_sid,
        )
        spoken.append(final.body.decode())

        all_spoken = " ".join(spoken)
        assert all_spoken.count("Most importantly") == 1  # once per call

        stored = repo.load_session_by_call(call_sid)
        record = stored.channel_metadata["workflow_final_result"]["structured_output"][
            "intake_record"
        ]
        assert record["flags"][0] == "injuries_reported"
        assert record["human_review_required"] is True


# ---------------------------------------------------------------------------
# Malicious / misconfigured definitions
# ---------------------------------------------------------------------------


def test_spec_cannot_claim_reserved_healthcare_id(tmp_path):
    bad = dict(TOY_SPEC, workflow_id="healthcare_triage_v1")
    (tmp_path / "evil.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.config.EXTRA_WORKFLOW_DEFINITIONS_DIR", str(tmp_path))
        reset_workflow_registry()
        registry = ensure_default_workflows_registered()
        report = register_spec_definitions(registry)
    assert any("evil.json" == name for name, _ in report.rejected)
    assert "healthcare_triage_v1" not in report.loaded
    # The real healthcare workflow is still the registered one.
    from src.verticals.healthcare.workflow import HealthcareTriageWorkflow

    assert isinstance(registry.get("healthcare_triage_v1"), HealthcareTriageWorkflow)
    reset_workflow_registry()


def test_spec_cannot_claim_healthcare_vertical():
    with pytest.raises(ValueError):
        WorkflowSpec.model_validate(dict(TOY_SPEC, vertical="healthcare"))


def test_spec_with_unknown_hook_is_rejected(tmp_path):
    bad = dict(TOY_SPEC, completion_rules="totally_not_registered_v1")
    (tmp_path / "unknown_hook.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.config.EXTRA_WORKFLOW_DEFINITIONS_DIR", str(tmp_path))
        reset_workflow_registry()
        registry = ensure_default_workflows_registered()
        report = register_spec_definitions(registry)
    assert any("unknown_hook.json" == name for name, _ in report.rejected)
    reset_workflow_registry()


def test_registry_refuses_replacing_reserved_workflow():
    reset_workflow_registry()
    registry = ensure_default_workflows_registered()

    class _Impostor(SpecDrivenWorkflow):
        def get_definition(self):
            definition = super().get_definition()
            definition.workflow_id = "healthcare_triage_v1"
            definition.vertical = "healthcare"
            return definition

    impostor = _Impostor(WorkflowSpec.model_validate(TOY_SPEC))
    with pytest.raises(ValueError):
        registry.register(impostor)
    reset_workflow_registry()


def test_engine_overlay_never_touches_healthcare_results():
    from src.platform.workflows.router import _enforce_turn_safety_overlay
    from src.platform.workflows.schemas import WorkflowInput

    result = WorkflowTurnResult(
        assistant_text="Please describe your symptoms.",
        stage="DYNAMIC",
        should_continue=True,
        should_finalize=False,
        escalation_required=False,
        updated_state={},
    )
    context = WorkflowContext(
        session_id="hc",
        vertical="healthcare",
        workflow_id="healthcare_triage_v1",
        workflow_version="v1",
    )
    out = _enforce_turn_safety_overlay(
        context,
        WorkflowInput(user_text="my chest hurts and I am bleeding"),
        result,
    )
    # Healthcare's own safety stack governs — the overlay must not modify.
    assert out.assistant_text == "Please describe your symptoms."
    assert out.rules_triggered == []


def test_reserved_ids_documented():
    assert "healthcare_triage_v1" in RESERVED_WORKFLOW_IDS


# ---------------------------------------------------------------------------
# Routing + Birchwood spec exposure
# ---------------------------------------------------------------------------


def test_workflow_phone_routes_resolves_registered_workflow():
    from src.platform.workflows.router import WorkflowRouteResolver

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "src.config.WORKFLOW_PHONE_ROUTES",
            {"+1 (555) 555-0140": "birchwood_collision_intake_v1"},
        )
        route = WorkflowRouteResolver(repository=None).resolve("+15555550140")
    assert route.workflow_id == "birchwood_collision_intake_v1"
    assert route.audit_metadata["configured_key"] == "WORKFLOW_PHONE_ROUTES"


def test_workflow_phone_routes_ignores_unknown_workflow():
    from src.platform.workflows.router import WorkflowRouteResolver

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "src.config.WORKFLOW_PHONE_ROUTES",
            {"+15555550199": "does_not_exist_v1"},
        )
        mp.setattr("src.config.ENABLE_DEFAULT_WORKFLOW_ROUTE", True)
        route = WorkflowRouteResolver(repository=None).resolve("+15555550199")
    # Falls through to the default route instead of crashing.
    assert route.workflow_id != "does_not_exist_v1"


def test_birchwood_exposes_complete_spec():
    from src.verticals.automotive_collision.constants import (
        BIRCHWOOD_COLLISION_OUTCOMES,
        BIRCHWOOD_COLLISION_REQUIRED_FIELDS,
    )
    from src.verticals.automotive_collision.workflow import (
        BirchwoodCollisionIntakeWorkflow,
    )

    workflow = BirchwoodCollisionIntakeWorkflow()
    spec = workflow.get_spec()
    assert spec is not None
    assert spec.workflow_id == "birchwood_collision_intake_v1"
    assert spec.required_fields == list(BIRCHWOOD_COLLISION_REQUIRED_FIELDS)
    assert spec.completion_rules == "automotive_collision_birchwood_rules_v1"
    assert "injury_safety_branch" in spec.safety_hooks
    assert spec.dashboard_display_fields  # dashboard contract for PR 4
    # Every routing outcome has a recommended action for the shop.
    for outcome in BIRCHWOOD_COLLISION_OUTCOMES:
        assert outcome in spec.recommended_actions
    # The spec's stages mirror the live scripted intake exactly.
    live = workflow.get_scripted_intake_definition()
    assert [s.field_name for s in spec.stages] == [s.field_name for s in live.stages]


def test_healthcare_has_no_spec_and_stays_uniform():
    reset_workflow_registry()
    registry = ensure_default_workflows_registered()
    healthcare = registry.get("healthcare_triage_v1")
    assert healthcare.get_spec() is None
    assert healthcare.get_definition().vertical == "healthcare"
    reset_workflow_registry()


def test_safe_format_never_raises():
    assert safe_format("Hi {name}, {missing}!", {"name": "Pat"}) == "Hi Pat, unknown!"
    assert safe_format("Broken {", {}) == "Broken {"
