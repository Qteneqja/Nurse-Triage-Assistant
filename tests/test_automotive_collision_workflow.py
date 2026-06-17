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
    BIRCHWOOD_COLLISION_STATUS,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.automotive_collision.extraction import (
    AutomotiveCollisionExtractionAgent,
)
from src.verticals.automotive_collision.workflow import (
    BirchwoodCollisionIntakeWorkflow,
)
from src.verticals.healthcare.constants import HEALTHCARE_TRIAGE_WORKFLOW_ID


def _context(session_id: str = "collision-session") -> WorkflowContext:
    return WorkflowContext(
        session_id=session_id,
        vertical=AUTOMOTIVE_COLLISION_VERTICAL,
        workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
        workflow_version="v1",
    )


def _scripted_fields(**overrides) -> dict:
    base = {
        "is_drivable": "yes",
        "damage_type": "front bumper body damage",
        "vehicle_year": 2020,
        "rebuilt_salvage_status": "no",
        "caller_name": "John Smith",
        "phone": "+12045550123",
        "email": "john.smith@example.com",
        "address": "123 Demo Street, Winnipeg, MB R3C 1A1",
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "license_plate": "ABC123",
        "incident_description": "Hit a pole in a parking lot.",
        "incident_datetime": "2026-05-17 14:00",
        "incident_location": "parking lot on Demo Street",
        "injuries_state": "denied",
        "filing_insurance_claim": "yes",
        "claim_number": "CLM-2026-12345",
        "preferred_collision_center": "BIRCHWOOD_COLLISION_LOCATION_1",
    }
    base.update(overrides)
    return base


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


def test_workflow_is_registered():
    reset_workflow_registry()
    registry = ensure_default_workflows_registered()

    workflow = registry.get(BIRCHWOOD_COLLISION_WORKFLOW_ID)
    default_healthcare = registry.get_default_for_vertical("healthcare")

    assert isinstance(workflow, BirchwoodCollisionIntakeWorkflow)
    assert workflow.get_definition().vertical == AUTOMOTIVE_COLLISION_VERTICAL
    assert (
        default_healthcare.get_definition().workflow_id == HEALTHCARE_TRIAGE_WORKFLOW_ID
    )


def test_route_resolves_from_birchwood_collision_phone_number(monkeypatch):
    resolver = WorkflowRouteResolver(repository=None)
    monkeypatch.setattr(
        "src.platform.workflows.router.config.BIRCHWOOD_COLLISION_PHONE_NUMBER",
        BIRCHWOOD_COLLISION_PHONE_PLACEHOLDER,
        raising=False,
    )
    # The live pilot route applies when the number is configured for it. (The
    # default now routes to the minimal workflow — see test_birchwood_collision
    # _min_routing.py.) The live WORKFLOW itself is unchanged.
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


def test_scripted_intake_definition_exists():
    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()

    assert intake.intro_text
    # PR 2: narrative-first — the story comes before any structured question,
    # then the injury check (Invariant 3), then targeted vehicle gap-fill.
    assert [stage.field_name for stage in intake.stages[:4]] == [
        "incident_description",
        "injuries_state",
        "is_drivable",
        "damage_type",
    ]
    # The readback confirmation closes the scripted flow.
    assert intake.stages[-2].field_name == "confirmation_ack"
    assert intake.stages[-2].dynamic_prompt is True
    assert intake.stages[-1].field_name == "correction_note"
    # Voice pass: the greeting speaks in Birchwood's brand voice (ORCA stays
    # vendor-facing in materials/metadata, not in the customer greeting).
    assert "Birchwood Automotive Group" in intake.intro_text
    assert "recorded for training and quality purposes" in intake.intro_text


@pytest.mark.asyncio
async def test_non_drivable_vehicle_transfers_to_collision_center():
    result = await _run_case(is_drivable="no, it needs towing")

    assert result.recommended_disposition == "TRANSFER_COLLISION_CENTER"
    assert "non_drivable_transfer" in result.rules_triggered[0] or (
        "non_drivable_transfer" in _record(result)["flags"]
    )
    assert _record(result)["transfer_department"] == "collision_center"


@pytest.mark.asyncio
async def test_unsure_drivability_transfers_to_collision_center():
    result = await _run_case(is_drivable="I am not sure")

    assert result.recommended_disposition == "TRANSFER_COLLISION_CENTER"
    assert "non_drivable_transfer" in _record(result)["flags"]


@pytest.mark.asyncio
async def test_glass_only_damage_transfers_to_glass_department():
    result = await _run_case(damage_type="windshield glass only")

    assert result.recommended_disposition == "TRANSFER_GLASS_DEPARTMENT"
    assert _record(result)["transfer_department"] == "glass_department"
    assert "glass_only_transfer" in _record(result)["flags"]


@pytest.mark.asyncio
async def test_glass_plus_body_damage_continues_intake():
    result = await _run_case(damage_type="windshield and front bumper body damage")

    assert result.recommended_disposition == "COMPLETED_INTAKE"
    assert _record(result)["incident"]["body_damage"] is True


@pytest.mark.asyncio
async def test_2011_or_older_vehicle_declines_politely():
    result = await _run_case(vehicle_year=2011)

    assert result.recommended_disposition == "DECLINED_VEHICLE_YEAR"
    assert "vehicles from 2012 and newer" in result.assistant_text
    assert "vehicle_year_declined" in _record(result)["flags"]


@pytest.mark.asyncio
async def test_2012_or_newer_vehicle_continues():
    result = await _run_case(vehicle_year=2012)

    assert result.recommended_disposition == "COMPLETED_INTAKE"


@pytest.mark.asyncio
async def test_rebuilt_salvage_vehicle_declines_politely():
    result = await _run_case(rebuilt_salvage_status="yes, it is rebuilt")

    assert result.recommended_disposition == "DECLINED_REBUILT_SALVAGE"
    assert "rebuilt or salvage title vehicles" in result.assistant_text
    assert "rebuilt_salvage_declined" in _record(result)["flags"]


@pytest.mark.asyncio
async def test_unsure_rebuilt_continues_with_staff_review_flag():
    result = await _run_case(rebuilt_salvage_status="not sure")

    assert result.recommended_disposition == "COMPLETED_INTAKE"
    assert "staff_review_rebuilt_status" in _record(result)["flags"]
    assert _record(result)["human_review_required"] is True


@pytest.mark.asyncio
async def test_private_pay_skips_claim_number_and_flags_private_pay():
    result = await _run_case(
        filing_insurance_claim="no, private pay",
        claim_number="",
    )

    assert result.recommended_disposition == "COMPLETED_INTAKE"
    assert "private_pay" in _record(result)["flags"]
    assert "missing_claim_number" not in _record(result)["flags"]


@pytest.mark.asyncio
async def test_filing_insurance_without_claim_number_flags_callback_needed():
    result = await _run_case(filing_insurance_claim="yes", claim_number="")

    assert result.recommended_disposition == "INCOMPLETE_CALLBACK_NEEDED"
    assert "missing_claim_number" in _record(result)["flags"]
    assert _record(result)["callback_needed"] is True


@pytest.mark.asyncio
async def test_missing_license_plate_is_optional_and_does_not_flag_callback():
    # PR 2: the plate is optional — captured when offered, never a reason to
    # mark an otherwise-complete intake incomplete.
    result = await _run_case(license_plate="")

    assert result.recommended_disposition == "COMPLETED_INTAKE"
    assert "missing_license_plate" not in _record(result)["flags"]


@pytest.mark.asyncio
async def test_vw_offers_all_three_location_placeholders():
    result = await _run_case(
        vehicle_make="Volkswagen",
        preferred_collision_center="",
    )

    record = _record(result)
    assert record["is_vw"] is True
    assert record["location"]["available_centers"] == [
        "BIRCHWOOD_COLLISION_LOCATION_1",
        "BIRCHWOOD_COLLISION_LOCATION_2",
        "BIRCHWOOD_COLLISION_LOCATION_3",
    ]
    assert "vw_location_choice" in record["flags"]


@pytest.mark.asyncio
async def test_luxury_brand_auto_assigns_luxury_location():
    result = await _run_case(vehicle_make="BMW", preferred_collision_center="")

    record = _record(result)
    assert result.recommended_disposition == "COMPLETED_INTAKE"
    assert record["preferred_collision_center"] == "BIRCHWOOD_COLLISION_LUXURY_LOCATION"
    assert "luxury_auto_assigned" in record["flags"]


@pytest.mark.asyncio
async def test_customer_pressing_zero_transfers():
    workflow = BirchwoodCollisionIntakeWorkflow()
    context = _context("collision-zero")
    session = _session_for_fields(workflow, context)

    result = await workflow.handle_turn(
        context,
        WorkflowInput(user_text="0", session_state=session.model_dump(mode="json")),
    )

    assert result.recommended_disposition == "TRANSFER_COLLISION_CENTER"
    assert "caller_requested_transfer" in _record(result)["flags"]


@pytest.mark.asyncio
async def test_possible_duplicate_flag_works():
    result = await _run_case(already_spoke_to_someone=True)

    assert "possible_duplicate" in _record(result)["flags"]


def test_extraction_produces_required_fields():
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

    required_fields = [
        "workflow_id",
        "vertical",
        "powered_by",
        "client_target",
        "status",
        "caller_name",
        "phone",
        "email",
        "address",
        "vehicle_year",
        "vehicle_make",
        "vehicle_model",
        "license_plate",
        "is_drivable",
        "damage_type",
        "glass_only",
        "body_damage",
        "incident_description",
        "filing_insurance_claim",
        "claim_number",
        "private_pay",
        "preferred_collision_center",
        "is_luxury",
        "is_vw",
        "is_rebuilt_or_salvage",
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
