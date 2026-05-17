import re

import pytest

from src.orchestrator.schemas import OrchestratorSession
from src.platform.extraction.service import ExtractionService
from src.platform.workflows.registry import (
    ensure_default_workflows_registered,
    reset_workflow_registry,
)
from src.platform.workflows.router import WorkflowRouteResolver
from src.platform.workflows.schemas import WorkflowContext, WorkflowInput
from src.verticals.healthcare.constants import HEALTHCARE_TRIAGE_WORKFLOW_ID
from src.verticals.insurance.constants import (
    INSURANCE_CLAIMS_FNOL_WORKFLOW_ID,
    INSURANCE_FNOL_PHONE_PLACEHOLDER,
    INSURANCE_VERTICAL,
)
from src.verticals.insurance.extraction import InsuranceClaimsExtractionAgent
from src.verticals.insurance.workflow import InsuranceClaimsFnolWorkflow


def _context(session_id: str = "insurance-session") -> WorkflowContext:
    return WorkflowContext(
        session_id=session_id,
        vertical=INSURANCE_VERTICAL,
        workflow_id=INSURANCE_CLAIMS_FNOL_WORKFLOW_ID,
        workflow_version="v1",
    )


def _scripted_fields(**overrides) -> dict[str, str]:
    base = {
        "caller_name": "Jordan Smith",
        "callback_number": "+15551230000",
        "policy_number": "POL-4421",
        "claim_type": "property damage",
        "loss_datetime": "2026-05-15 08:30",
        "loss_location": "123 Main Street, Winnipeg",
        "incident_summary": "A tree branch damaged the roof during a storm.",
    }
    base.update(overrides)
    return base


def _session_for_fields(
    workflow: InsuranceClaimsFnolWorkflow,
    context: WorkflowContext,
    **fields,
) -> OrchestratorSession:
    session = OrchestratorSession.model_validate(workflow.start_session(context))
    scripted = session.channel_metadata.setdefault("scripted_intake", {})
    scripted["fields"] = _scripted_fields(**fields)
    scripted["completed"] = True
    session.channel_metadata["stage"] = "DYNAMIC"
    return session


def test_insurance_workflow_registers_without_changing_healthcare_default():
    reset_workflow_registry()
    registry = ensure_default_workflows_registered()

    insurance_workflow = registry.get(INSURANCE_CLAIMS_FNOL_WORKFLOW_ID)
    default_healthcare = registry.get_default_for_vertical("healthcare")

    assert isinstance(insurance_workflow, InsuranceClaimsFnolWorkflow)
    assert insurance_workflow.get_definition().vertical == INSURANCE_VERTICAL
    assert insurance_workflow.get_definition().version == "v1"
    assert (
        default_healthcare.get_definition().workflow_id == HEALTHCARE_TRIAGE_WORKFLOW_ID
    )


def test_insurance_route_resolves_from_placeholder_phone_number(monkeypatch):
    resolver = WorkflowRouteResolver(repository=None)

    monkeypatch.setattr(
        "src.platform.workflows.router.config.INSURANCE_FNOL_PHONE_NUMBER",
        INSURANCE_FNOL_PHONE_PLACEHOLDER,
        raising=False,
    )

    route = resolver.resolve("+1 (555) 555-0130")

    assert route.vertical_key == INSURANCE_VERTICAL
    assert route.workflow_id == INSURANCE_CLAIMS_FNOL_WORKFLOW_ID
    assert route.workflow_version == "v1"
    assert route.fallback_used is False
    assert route.audit_metadata["routing_source"] == "configured_phone_number"


def test_missing_insurance_phone_number_does_not_crash_route_resolver(monkeypatch):
    resolver = WorkflowRouteResolver(repository=None)

    monkeypatch.setattr(
        "src.platform.workflows.router.config.INSURANCE_FNOL_PHONE_NUMBER",
        "",
        raising=False,
    )
    monkeypatch.setattr(
        "src.platform.workflows.router.config.ENABLE_DEFAULT_WORKFLOW_ROUTE",
        True,
        raising=False,
    )

    route = resolver.resolve("+15550009999")

    assert route.workflow_id == HEALTHCARE_TRIAGE_WORKFLOW_ID
    assert route.vertical_key == "healthcare"


def test_insurance_scripted_stages_are_exposed_in_order():
    intake = InsuranceClaimsFnolWorkflow().get_scripted_intake_definition()

    assert [stage.field_name for stage in intake.stages] == [
        "caller_name",
        "callback_number",
        "policy_number",
        "claim_type",
        "loss_datetime",
        "loss_location",
        "incident_summary",
    ]
    assert intake.stages[3].allowed_values == [
        "auto accident",
        "property damage",
        "water damage",
        "theft/loss",
        "glass/window damage",
        "liability/business claim",
        "other",
    ]


@pytest.mark.asyncio
async def test_insurance_dynamic_follow_up_starts_after_scripted_intake():
    workflow = InsuranceClaimsFnolWorkflow()
    context = _context("insurance-follow-up")
    session = _session_for_fields(
        workflow,
        context,
        claim_type="auto accident",
        incident_summary="Two cars collided in an intersection.",
    )

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text=session.channel_metadata["scripted_intake"]["fields"][
                "callback_number"
            ],
            session_state=session.model_dump(mode="json"),
        ),
    )

    assert result.should_continue is True
    assert result.should_finalize is False
    assert "hurt" in result.assistant_text.lower()


@pytest.mark.asyncio
async def test_auto_accident_with_injury_routes_to_urgent_adjuster_review():
    workflow = InsuranceClaimsFnolWorkflow()
    context = _context("insurance-auto-injury")
    session = _session_for_fields(
        workflow,
        context,
        claim_type="auto accident",
        incident_summary="Another driver hit my car at an intersection.",
    )

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text=(
                "Yes, one driver has neck pain, police and EMS are on scene, "
                "the car is not drivable, another vehicle was involved, and I have photos."
            ),
            session_state=session.model_dump(mode="json"),
        ),
    )

    assert result.should_finalize is True
    assert result.recommended_disposition in {
        "URGENT_ADJUSTER_REVIEW",
        "EMERGENCY_SERVICES_NOW",
    }


@pytest.mark.asyncio
async def test_active_fire_routes_to_emergency_services_now():
    workflow = InsuranceClaimsFnolWorkflow()
    context = _context("insurance-fire")
    session = _session_for_fields(
        workflow,
        context,
        claim_type="property damage",
        incident_summary="There is visible smoke and damage at the property.",
    )

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text=(
                "There is an active fire right now and the scene is unsafe. "
                "Please tell me what to do."
            ),
            session_state=session.model_dump(mode="json"),
        ),
    )

    assert result.should_finalize is True
    assert result.escalation_required is True
    assert result.recommended_disposition == "EMERGENCY_SERVICES_NOW"
    assert "emergency services" in result.assistant_text.lower()


@pytest.mark.asyncio
async def test_normal_property_damage_routes_to_standard_claim_intake():
    workflow = InsuranceClaimsFnolWorkflow()
    context = _context("insurance-standard")
    session = _session_for_fields(
        workflow,
        context,
        claim_type="property damage",
        incident_summary="A fallen branch damaged part of the roof yesterday.",
    )

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text=(
                "No one is hurt, no emergency services were needed, the property is safe now, "
                "and I have photos ready."
            ),
            session_state=session.model_dump(mode="json"),
        ),
    )

    assert result.should_finalize is True
    assert result.recommended_disposition == "STANDARD_CLAIM_INTAKE"


@pytest.mark.asyncio
async def test_missing_key_information_routes_to_review_or_documents_needed():
    workflow = InsuranceClaimsFnolWorkflow()
    context = _context("insurance-missing-info")
    session = _session_for_fields(
        workflow,
        context,
        claim_type="theft/loss",
        loss_datetime="",
        loss_location="",
        incident_summary="Items are missing but I do not know when it happened.",
        policy_number="",
    )

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text=(
                "I am not sure when it happened, I do not have receipts, and I have not filed a report yet."
            ),
            session_state=session.model_dump(mode="json"),
        ),
    )

    assert result.should_finalize is True
    assert result.recommended_disposition in {"HUMAN_REVIEW", "DOCUMENTS_NEEDED"}


@pytest.mark.asyncio
async def test_information_only_caller_routes_to_information_only():
    workflow = InsuranceClaimsFnolWorkflow()
    context = _context("insurance-info-only")
    session = _session_for_fields(
        workflow,
        context,
        claim_type="other",
        incident_summary="The caller only wants to know the claims process.",
    )

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text=(
                "I just want to understand the process and I do not want to start a claim right now."
            ),
            session_state=session.model_dump(mode="json"),
        ),
    )

    assert result.should_finalize is True
    assert result.recommended_disposition == "INFORMATION_ONLY"


def test_insurance_extraction_produces_required_structured_fields():
    workflow = InsuranceClaimsFnolWorkflow()
    context = _context("insurance-extraction")
    session = _session_for_fields(
        workflow,
        context,
        claim_type="water damage",
        incident_summary="Water leaked from an upstairs bathroom into the ceiling.",
    )
    session.channel_metadata["insurance_claim"] = {
        "relationship_to_policyholder": "self",
        "preferred_callback_method": "phone",
        "emergency_or_safety_issue": False,
        "injuries_mentioned": False,
        "emergency_services_involved": False,
        "police_or_fire_report": False,
        "property_secure": True,
        "mitigation_needed": True,
        "documents_available": True,
        "missing_information": [],
        "recommended_routing": "URGENT_ADJUSTER_REVIEW",
        "confidence": 0.89,
        "human_review_required": False,
        "disclaimers_given": [
            "A licensed broker/adjuster can confirm coverage.",
            "This does not guarantee claim approval.",
        ],
    }

    final_result = workflow.build_final_result_from_session(context, session)
    original_disposition = final_result.final_disposition

    service = ExtractionService()
    service.register(
        INSURANCE_CLAIMS_FNOL_WORKFLOW_ID,
        InsuranceClaimsExtractionAgent(),
    )
    extraction = service.extract(
        transcript=[{"role": "caller", "text": "We have photos and the leak stopped."}],
        final_result=final_result,
        workflow_context=context,
        extraction_schema=workflow.get_extraction_schema(),
    )

    assert final_result.final_disposition == original_disposition
    for field_name in [
        "workflow_id",
        "vertical",
        "claim_type",
        "caller_name",
        "callback_number",
        "policy_number",
        "loss_datetime",
        "loss_location",
        "incident_summary",
        "emergency_or_safety_issue",
        "injuries_mentioned",
        "emergency_services_involved",
        "police_or_fire_report",
        "property_secure",
        "mitigation_needed",
        "documents_available",
        "missing_information",
        "recommended_routing",
        "confidence",
        "human_review_required",
        "disclaimers_given",
    ]:
        assert field_name in extraction.entities


@pytest.mark.asyncio
async def test_insurance_assistant_does_not_promise_coverage_approval_or_payout():
    workflow = InsuranceClaimsFnolWorkflow()
    context = _context("insurance-language")
    session = _session_for_fields(
        workflow,
        context,
        claim_type="glass/window damage",
        incident_summary="A rock cracked the front window last night.",
    )

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text=(
                "The opening is secure, there is no emergency, and I have photos of the damage."
            ),
            session_state=session.model_dump(mode="json"),
        ),
    )

    combined = result.assistant_text.lower()
    assert not re.search(r"\b(will|guarantee|guaranteed)\b.*\bcover", combined)
    assert "approved" not in combined
    assert "payout" not in combined
    assert "legal advice" not in combined
