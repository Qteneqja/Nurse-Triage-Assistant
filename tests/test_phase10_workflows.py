import pytest
from unittest.mock import AsyncMock, MagicMock

from src.orchestrator.orchestrator import Orchestrator
from src.platform.workflows.registry import (
    WorkflowNotFoundError,
    ensure_default_workflows_registered,
    reset_workflow_registry,
)
from src.platform.workflows.router import WorkflowEngine
from src.platform.workflows.schemas import WorkflowContext, WorkflowInput
from src.verticals.healthcare.constants import (
    HEALTHCARE_DISPOSITIONS,
    HEALTHCARE_TRIAGE_WORKFLOW_ID,
)
from src.verticals.healthcare.workflow import HealthcareTriageWorkflow


def _context(session_id: str = "phase10-session") -> WorkflowContext:
    return WorkflowContext(
        session_id=session_id,
        vertical="healthcare",
        workflow_id=HEALTHCARE_TRIAGE_WORKFLOW_ID,
        workflow_version="v1",
    )


def test_healthcare_workflow_registers_by_default():
    reset_workflow_registry()
    registry = ensure_default_workflows_registered()

    workflow = registry.get(HEALTHCARE_TRIAGE_WORKFLOW_ID)
    assert isinstance(workflow, HealthcareTriageWorkflow)
    assert workflow.get_definition().workflow_id == HEALTHCARE_TRIAGE_WORKFLOW_ID


def test_missing_workflow_raises_clean_error():
    reset_workflow_registry()
    registry = ensure_default_workflows_registered()

    with pytest.raises(WorkflowNotFoundError, match="not registered"):
        registry.get("missing_workflow_v1")


def test_healthcare_workflow_definition_is_platform_ready():
    workflow = HealthcareTriageWorkflow()
    definition = workflow.get_definition()

    assert definition.vertical == "healthcare"
    assert definition.version == "v1"
    assert definition.display_name == "Healthcare Triage"
    assert definition.default_output_type == "SBAR"
    assert "SBAR" in definition.supported_output_types
    assert definition.supports_post_call_extraction is True
    assert set(HEALTHCARE_DISPOSITIONS) == {
        "ER_NOW",
        "URGENT",
        "SCHEDULE",
        "SELF_CARE",
        "HUMAN_REVIEW",
    }


def test_healthcare_workflow_exposes_scripted_intake_definition():
    intake = HealthcareTriageWorkflow().get_scripted_intake_definition()

    assert intake is not None
    assert [stage.field_name for stage in intake.stages] == [
        "caller_name",
        "caller_age",
        "caller_sex",
        "chief_complaint",
    ]
    assert intake.stages[2].hints == "male,female,prefer not to say"


@pytest.mark.asyncio
async def test_red_flag_escalation_works_through_workflow_interface():
    mock_llm = MagicMock()
    mock_llm.call = AsyncMock()
    workflow = HealthcareTriageWorkflow(orchestrator=Orchestrator(llm_client=mock_llm))
    context = _context()
    session_state = workflow.start_session(context)

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text="I can't breathe at all",
            session_state=session_state,
        ),
    )

    assert result.escalation_required is True
    assert result.should_continue is False
    assert result.recommended_disposition == "ER_NOW"
    assert "pre_check:critical_flag" in result.rules_triggered
    mock_llm.call.assert_not_called()


@pytest.mark.asyncio
async def test_workflow_engine_initializes_default_registry_after_reset():
    reset_workflow_registry()

    result = await WorkflowEngine().handle_turn(
        _context("engine-session"),
        WorkflowInput(user_text="I can't breathe at all", session_state={}),
    )

    assert result.escalation_required is True
    assert result.recommended_disposition == "ER_NOW"
