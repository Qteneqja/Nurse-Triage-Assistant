import pytest

from src.orchestrator.schemas import OrchestratorSession
from src.platform.extraction.service import ExtractionService
from src.platform.workflows.registry import (
    ensure_default_workflows_registered,
    reset_workflow_registry,
)
from src.platform.workflows.schemas import WorkflowContext, WorkflowInput
from src.verticals.healthcare.constants import HEALTHCARE_TRIAGE_WORKFLOW_ID
from src.verticals.property_management.constants import (
    PROPERTY_MAINTENANCE_WORKFLOW_ID,
)
from src.verticals.property_management.extraction import (
    PropertyMaintenanceExtractionAgent,
)
from src.verticals.property_management.rules import classify_maintenance_request
from src.verticals.property_management.schemas import MaintenanceIntake
from src.verticals.property_management.workflow import (
    PropertyManagementMaintenanceWorkflow,
)


def _context(session_id: str = "property-session") -> WorkflowContext:
    return WorkflowContext(
        session_id=session_id,
        vertical="property_management",
        workflow_id=PROPERTY_MAINTENANCE_WORKFLOW_ID,
        workflow_version="v1",
    )


def test_property_workflow_registers_without_changing_healthcare_default():
    reset_workflow_registry()
    registry = ensure_default_workflows_registered()

    property_workflow = registry.get(PROPERTY_MAINTENANCE_WORKFLOW_ID)
    default_healthcare = registry.get_default_for_vertical("healthcare")

    assert isinstance(property_workflow, PropertyManagementMaintenanceWorkflow)
    assert property_workflow.get_definition().vertical == "property_management"
    assert property_workflow.get_definition().version == "v1"
    assert (
        default_healthcare.get_definition().workflow_id == HEALTHCARE_TRIAGE_WORKFLOW_ID
    )


def test_property_scripted_stages_are_exposed_in_order():
    intake = PropertyManagementMaintenanceWorkflow().get_scripted_intake_definition()

    assert [stage.field_name for stage in intake.stages] == [
        "caller_name",
        "property_address",
        "unit_number",
        "issue_type",
        "issue_description",
        "access_permission",
        "callback_phone",
    ]
    assert intake.stages[3].allowed_values == [
        "plumbing",
        "heat",
        "electrical",
        "lockout",
        "appliance",
        "noise",
        "other",
    ]


def test_property_rules_classify_required_urgencies():
    cases = [
        ("plumbing", "active flooding in the unit", "EMERGENCY"),
        ("other", "I smell gas in the kitchen", "EMERGENCY"),
        ("electrical", "there are electrical sparks from the outlet", "EMERGENCY"),
        ("heat", "no heat in winter and it is freezing", "EMERGENCY"),
        ("hot water", "we have no hot water", "SAME_DAY"),
        ("plumbing", "the bathroom has a dripping faucet", "SCHEDULED_REPAIR"),
        ("", "", "HUMAN_REVIEW"),
    ]

    for issue_type, description, expected in cases:
        result = classify_maintenance_request(
            MaintenanceIntake(
                issue_type=issue_type,
                issue_description=description,
            )
        )
        assert result.disposition == expected


@pytest.mark.asyncio
async def test_property_workflow_finalizes_into_maintenance_summary():
    workflow = PropertyManagementMaintenanceWorkflow()
    context = _context()
    session = OrchestratorSession.model_validate(workflow.start_session(context))
    session.channel_metadata["scripted_intake"]["fields"] = {
        "caller_name": "Jordan Smith",
        "property_address": "123 Main Street",
        "unit_number": "4B",
        "issue_type": "plumbing",
        "issue_description": "There is active flooding from a burst pipe.",
        "access_permission": "yes",
        "callback_phone": "+15551234567",
    }

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text="+15551234567",
            session_state=session.model_dump(mode="json"),
        ),
    )

    assert result.should_finalize is True
    assert result.escalation_required is True
    assert result.recommended_disposition == "EMERGENCY"
    work_order = result.updated_state["channel_metadata"]["workflow_final_result"][
        "structured_output"
    ]["work_order"]
    assert work_order["property_address"] == "123 Main Street"
    assert work_order["vendor_type"] == "plumbing"


def test_property_extraction_is_structured_and_read_only():
    workflow = PropertyManagementMaintenanceWorkflow()
    context = _context()
    session = OrchestratorSession.model_validate(workflow.start_session(context))
    session.channel_metadata["scripted_intake"]["fields"] = {
        "issue_type": "plumbing",
        "issue_description": "minor leak under the sink",
        "property_address": "55 Pine Road",
        "unit_number": "2A",
        "access_permission": "no",
    }
    final_result = workflow.build_final_result_from_session(context, session)
    original_disposition = final_result.final_disposition

    service = ExtractionService()
    service.register(
        PROPERTY_MAINTENANCE_WORKFLOW_ID, PropertyMaintenanceExtractionAgent()
    )
    extraction = service.extract(
        transcript=[{"role": "caller", "text": "I am frustrated about a leak"}],
        final_result=final_result,
        workflow_context=context,
        extraction_schema=workflow.get_extraction_schema(),
    )

    assert final_result.final_disposition == original_disposition
    assert extraction.entities["issue_type"] == "plumbing"
    assert extraction.entities["property_address"] == "55 Pine Road"
    assert extraction.entities["urgency"] == "SAME_DAY"
    assert extraction.entities["tenant_sentiment"] == "frustrated"


def test_property_extraction_handles_partial_fields():
    workflow = PropertyManagementMaintenanceWorkflow()
    context = _context("partial-property")
    session = OrchestratorSession.model_validate(workflow.start_session(context))
    session.channel_metadata["scripted_intake"]["fields"] = {
        "issue_description": "dripping faucet",
    }
    final_result = workflow.build_final_result_from_session(context, session)

    extraction = PropertyMaintenanceExtractionAgent().extract(
        transcript=[],
        final_result=final_result,
        workflow_context=context,
        extraction_schema=None,
    )

    assert extraction.entities["urgency"] == "SCHEDULED_REPAIR"
    assert extraction.confidence_score == final_result.confidence_score
