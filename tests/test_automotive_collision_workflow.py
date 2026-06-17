"""Tests for the MINIMAL Birchwood collision intake workflow.

``birchwood_collision_intake_v1`` is now a pure-intake workflow: it collects
only what a collision specialist needs and hands off. It does NOT triage,
decide, adjudicate, transfer, or decline. ``classify_collision_intake`` returns
exactly two outcomes:

  - all required fields present  -> COMPLETED_INTAKE
  - a required field missing     -> INCOMPLETE_CALLBACK_NEEDED

Flags are descriptive data for the specialist (``needs_tow``, ``private_pay``,
``mpi_claim_in_hand`` / ``mpi_claim_open_no_number``), never routing decisions.
The reactive emergency reflex for a spontaneous injury mention is the SHARED
platform overlay (router) and is exercised elsewhere; collision intake never
asks about injuries.
"""

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
from src.verticals.automotive_collision.constants import (
    AUTOMOTIVE_COLLISION_VERTICAL,
    BIRCHWOOD_COLLISION_CLIENT_TARGET,
    BIRCHWOOD_COLLISION_DISPLAY_NAME,
    BIRCHWOOD_COLLISION_PHONE_PLACEHOLDER,
    BIRCHWOOD_COLLISION_POWERED_BY,
    BIRCHWOOD_COLLISION_REQUIRED_FIELDS,
    BIRCHWOOD_COLLISION_STATUS,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.automotive_collision.extraction import (
    AutomotiveCollisionExtractionAgent,
)
from src.verticals.automotive_collision.rules import classify_collision_intake
from src.verticals.automotive_collision.schemas import AutomotiveCollisionIntake
from src.verticals.automotive_collision.workflow import (
    BirchwoodCollisionIntakeWorkflow,
)
from src.verticals.healthcare.constants import HEALTHCARE_TRIAGE_WORKFLOW_ID


# Required fields for the minimal pure-intake flow (no injury, no rebuilt, no
# narrative, no datetime/location, no readback).
MINIMAL_REQUIRED_FIELDS = [
    "caller_name",
    "phone",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "damage_type",
    "is_drivable",
]


def _context(session_id: str = "collision-session") -> WorkflowContext:
    return WorkflowContext(
        session_id=session_id,
        vertical=AUTOMOTIVE_COLLISION_VERTICAL,
        workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
        workflow_version="v1",
    )


def _scripted_fields(**overrides) -> dict:
    """The minimal scripted fields a collision specialist needs."""
    base = {
        "caller_name": "John Smith",
        "phone": "+12045550123",
        "vehicle_year": 2020,
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "damage_type": "front bumper body damage",
        "is_drivable": "yes",
        "filing_insurance_claim": "yes",
        "claim_number": "CLM-2026-12345",
    }
    base.update(overrides)
    return base


def _intake(**overrides) -> AutomotiveCollisionIntake:
    """A complete minimal intake, with deterministic-typed values."""
    base = {
        "caller_name": "John Smith",
        "phone": "+12045550123",
        "vehicle_year": 2020,
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "damage_type": "front bumper body damage",
        "is_drivable": True,
        "filing_insurance_claim": True,
        "claim_number": "CLM-2026-12345",
    }
    base.update(overrides)
    return AutomotiveCollisionIntake(**base)


def _session_for_fields(
    workflow: BirchwoodCollisionIntakeWorkflow,
    context: WorkflowContext,
    **fields,
) -> OrchestratorSession:
    session = OrchestratorSession.model_validate(workflow.start_session(context))
    scripted = session.channel_metadata.setdefault("scripted_intake", {})
    scripted["fields"] = _scripted_fields(**fields)
    scripted["completed"] = True
    session.channel_metadata["stage"] = "DYNAMIC"
    return session


async def _run_case(**fields):
    workflow = BirchwoodCollisionIntakeWorkflow()
    context = _context("collision-case")
    session = _session_for_fields(workflow, context, **fields)
    return await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text="Done.",
            session_state=session.model_dump(mode="json"),
        ),
    )


def _record(result):
    return result.updated_state["channel_metadata"]["workflow_final_result"][
        "structured_output"
    ]["intake_record"]


# ---------------------------------------------------------------------------
# Registration / definition (still-valid — must not change the live pilot)
# ---------------------------------------------------------------------------


def test_workflow_is_registered():
    reset_workflow_registry()
    registry = ensure_default_workflows_registered()

    workflow = registry.get(BIRCHWOOD_COLLISION_WORKFLOW_ID)
    default_healthcare = registry.get_default_for_vertical("healthcare")

    assert isinstance(workflow, BirchwoodCollisionIntakeWorkflow)
    assert workflow.get_definition().vertical == AUTOMOTIVE_COLLISION_VERTICAL
    # Registering the collision workflow must not disturb the healthcare default.
    assert (
        default_healthcare.get_definition().workflow_id == HEALTHCARE_TRIAGE_WORKFLOW_ID
    )


def test_definition_is_minimal_pure_intake():
    definition = BirchwoodCollisionIntakeWorkflow().get_definition()

    assert definition.workflow_id == BIRCHWOOD_COLLISION_WORKFLOW_ID
    assert definition.vertical == AUTOMOTIVE_COLLISION_VERTICAL
    # The minimal required-field set drives the only two outcomes.
    assert definition.required_fields == MINIMAL_REQUIRED_FIELDS
    assert list(BIRCHWOOD_COLLISION_REQUIRED_FIELDS) == MINIMAL_REQUIRED_FIELDS


def test_route_resolves_from_birchwood_collision_phone_number(monkeypatch):
    resolver = WorkflowRouteResolver(repository=None)
    monkeypatch.setattr(
        "src.platform.workflows.router.config.BIRCHWOOD_COLLISION_PHONE_NUMBER",
        BIRCHWOOD_COLLISION_PHONE_PLACEHOLDER,
        raising=False,
    )
    monkeypatch.setattr(
        "src.platform.workflows.router.config.BIRCHWOOD_COLLISION_WORKFLOW_ID",
        BIRCHWOOD_COLLISION_WORKFLOW_ID,
        raising=False,
    )

    route = resolver.resolve("+1 (555) 555-0140")

    assert route.vertical_key == AUTOMOTIVE_COLLISION_VERTICAL
    assert route.workflow_id == BIRCHWOOD_COLLISION_WORKFLOW_ID
    assert route.config_json["display_name"] == BIRCHWOOD_COLLISION_DISPLAY_NAME
    assert route.config_json["powered_by"] == BIRCHWOOD_COLLISION_POWERED_BY
    assert route.config_json["client_target"] == BIRCHWOOD_COLLISION_CLIENT_TARGET
    assert route.config_json["status"] == BIRCHWOOD_COLLISION_STATUS


def test_missing_birchwood_phone_number_does_not_crash(monkeypatch):
    resolver = WorkflowRouteResolver(repository=None)
    monkeypatch.setattr(
        "src.platform.workflows.router.config.BIRCHWOOD_COLLISION_PHONE_NUMBER",
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


def test_scripted_intake_is_minimal_pure_intake():
    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()

    assert intake.intro_text
    field_names = [stage.field_name for stage in intake.stages]
    # Exactly the minimal scripted questions, in order: contact, vehicle,
    # damage, drivability, then optional MPI claim status.
    assert field_names == [
        "caller_name",
        "phone",
        "vehicle_year",
        "vehicle_make",
        "vehicle_model",
        "damage_type",
        "is_drivable",
        "filing_insurance_claim",
        "claim_number",
    ]
    # No injury question (the reactive reflex is the shared platform overlay),
    # no narrative, no rebuilt/salvage, no datetime/location, no readback.
    assert not any("injur" in name.lower() for name in field_names)
    assert "incident_description" not in field_names
    assert "rebuilt_salvage_status" not in field_names
    assert "incident_datetime" not in field_names
    assert "incident_location" not in field_names
    assert "confirmation_ack" not in field_names
    assert "correction_note" not in field_names
    # Required questions are required; the MPI claim pair is optional.
    by_field = {stage.field_name: stage for stage in intake.stages}
    for field in MINIMAL_REQUIRED_FIELDS:
        assert by_field[field].required is True
    assert by_field["filing_insurance_claim"].required is False
    assert by_field["claim_number"].required is False
    # Voice pass: the greeting speaks in Birchwood's brand voice.
    assert "Birchwood Automotive Group" in intake.intro_text
    assert "recorded for training and quality purposes" in intake.intro_text


# ---------------------------------------------------------------------------
# classify_collision_intake — the only two outcomes + descriptive flags
# ---------------------------------------------------------------------------


def test_complete_intake_is_completed():
    assessment = classify_collision_intake(_intake())

    assert assessment.outcome == "COMPLETED_INTAKE"
    assert assessment.missing_information == []
    assert assessment.callback_needed is False


@pytest.mark.parametrize("field", MINIMAL_REQUIRED_FIELDS)
def test_missing_required_field_is_incomplete_callback(field):
    assessment = classify_collision_intake(_intake(**{field: None}))

    assert assessment.outcome == "INCOMPLETE_CALLBACK_NEEDED"
    assert field in assessment.missing_information
    assert assessment.callback_needed is True


def test_non_drivable_flags_needs_tow_without_transferring():
    # is_drivable False is descriptive data (needs a tow), NOT a transfer.
    assessment = classify_collision_intake(_intake(is_drivable=False))

    assert assessment.outcome == "COMPLETED_INTAKE"
    assert "needs_tow" in assessment.flags


def test_private_pay_flags_private_pay():
    assessment = classify_collision_intake(
        _intake(filing_insurance_claim=False, claim_number=None)
    )

    assert assessment.outcome == "COMPLETED_INTAKE"
    assert assessment.private_pay is True
    assert "private_pay" in assessment.flags


def test_mpi_claim_status_is_descriptive_data_only():
    in_hand = classify_collision_intake(_intake(claim_number="CLM-1"))
    assert "mpi_claim_in_hand" in in_hand.flags

    no_number = classify_collision_intake(_intake(claim_number=None))
    assert "mpi_claim_open_no_number" in no_number.flags


# ---------------------------------------------------------------------------
# End-to-end via handle_turn — minimal flow only ever completes or callbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_run_completes_intake():
    result = await _run_case()

    assert result.recommended_disposition == "COMPLETED_INTAKE"
    assert result.escalation_required is False


@pytest.mark.asyncio
async def test_missing_required_field_run_requests_callback():
    result = await _run_case(vehicle_make="")

    assert result.recommended_disposition == "INCOMPLETE_CALLBACK_NEEDED"
    assert "vehicle_make" in _record(result)["missing_information"]
    assert _record(result)["callback_needed"] is True


@pytest.mark.asyncio
async def test_non_drivable_run_completes_with_needs_tow():
    result = await _run_case(is_drivable="no, it needs towing")

    assert result.recommended_disposition == "COMPLETED_INTAKE"
    assert "needs_tow" in _record(result)["flags"]


@pytest.mark.asyncio
async def test_private_pay_run_completes_with_private_pay_flag():
    result = await _run_case(
        filing_insurance_claim="no, private pay",
        claim_number="",
    )

    assert result.recommended_disposition == "COMPLETED_INTAKE"
    assert "private_pay" in _record(result)["flags"]


# ---------------------------------------------------------------------------
# Record / extraction shape (unchanged — just fewer fields populated)
# ---------------------------------------------------------------------------


def test_extraction_produces_core_required_fields():
    workflow = BirchwoodCollisionIntakeWorkflow()
    context = _context("collision-extraction")
    session = _session_for_fields(workflow, context)
    final_result = workflow.build_final_result_from_session(context, session)

    service = ExtractionService()
    service.register(
        BIRCHWOOD_COLLISION_WORKFLOW_ID,
        AutomotiveCollisionExtractionAgent(),
    )
    extraction = service.extract(
        transcript=[{"role": "caller", "text": "The front bumper is damaged."}],
        final_result=final_result,
        workflow_context=context,
        extraction_schema=workflow.get_extraction_schema(),
    )

    # The minimal flow populates the collision-specialist core plus the
    # platform/branding metadata. (Optional fields like preferred_collision_center
    # are simply unset now, so the extractor omits them.)
    required_fields = [
        "workflow_id",
        "vertical",
        "powered_by",
        "client_target",
        "status",
        "caller_name",
        "phone",
        "vehicle_year",
        "vehicle_make",
        "vehicle_model",
        "is_drivable",
        "damage_type",
        "glass_only",
        "body_damage",
        "filing_insurance_claim",
        "claim_number",
        "private_pay",
        "flags",
        "missing_information",
        "recommended_routing",
        "callback_needed",
        "transcript_summary",
        "disclaimers_given",
        "confidence",
        "human_review_required",
    ]
    for field_name in required_fields:
        assert field_name in extraction.entities


@pytest.mark.asyncio
async def test_assistant_language_avoids_forbidden_promises():
    result = await _run_case()

    combined = result.assistant_text.lower()
    assert not re.search(r"\brepair estimate\b.*\b(guaranteed|approved)", combined)
    assert "coverage " + "approved" not in combined
    assert "claim " + "approved" not in combined
    assert "definitely " + "covered" not in combined
    assert "we will " + "pay" not in combined


@pytest.mark.asyncio
async def test_completed_intake_uses_warm_non_promissory_closing():
    result = await _run_case()

    combined = result.assistant_text.lower()
    assert "here's what happens next" in combined
    assert "doesn't confirm coverage, pricing, or an appointment yet" in combined


def test_display_metadata_preserves_orca_platform_and_birchwood_target():
    definition = BirchwoodCollisionIntakeWorkflow().get_definition()

    assert definition.display_name == BIRCHWOOD_COLLISION_DISPLAY_NAME
    assert definition.metadata["powered_by"] == "ORCA"
    assert definition.metadata["client_target"] == "Birchwood Automotive Group"
    assert definition.metadata["status"] == "demo/pilot"
    assert not definition.display_name.startswith("ORCA -")
