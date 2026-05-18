from deepeval.test_case import LLMTestCase
import pytest

from src.evals.healthcare_eval_scorers import EvalScoreResult
from src.platform.workflows.schemas import WorkflowContext, WorkflowInput
from src.verticals.automotive_collision.constants import (
    AUTOMOTIVE_COLLISION_VERTICAL,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.automotive_collision.workflow import (
    BirchwoodCollisionIntakeWorkflow,
)
from tests.evals import assert_deepeval_score


AUTOMOTIVE_COLLISION_EVAL_CASES = [
    {
        "case_id": "birchwood_completed_intake_001",
        "fields": {},
        "dynamic_text": "Done.",
        "expected_disposition": "COMPLETED_INTAKE",
        "expected_rule": "automotive_collision:completed_intake",
    },
    {
        "case_id": "birchwood_non_drivable_transfer_001",
        "fields": {"is_drivable": "no, it needs towing"},
        "dynamic_text": "Done.",
        "expected_disposition": "TRANSFER_COLLISION_CENTER",
        "expected_rule": "automotive_collision:gate_1_drivability_transfer",
    },
    {
        "case_id": "birchwood_glass_only_transfer_001",
        "fields": {"damage_type": "windshield glass only"},
        "dynamic_text": "Done.",
        "expected_disposition": "TRANSFER_GLASS_DEPARTMENT",
        "expected_rule": "automotive_collision:gate_2_glass_only_transfer",
    },
    {
        "case_id": "birchwood_old_vehicle_decline_001",
        "fields": {"vehicle_year": 2010},
        "dynamic_text": "Done.",
        "expected_disposition": "DECLINED_VEHICLE_YEAR",
        "expected_rule": "automotive_collision:gate_3_vehicle_year_declined",
    },
    {
        "case_id": "birchwood_rebuilt_decline_001",
        "fields": {"rebuilt_salvage_status": "yes, rebuilt title"},
        "dynamic_text": "Done.",
        "expected_disposition": "DECLINED_REBUILT_SALVAGE",
        "expected_rule": "automotive_collision:gate_4_rebuilt_salvage_declined",
    },
    {
        "case_id": "birchwood_missing_claim_number_001",
        "fields": {"filing_insurance_claim": "yes", "claim_number": ""},
        "dynamic_text": "Done.",
        "expected_disposition": "INCOMPLETE_CALLBACK_NEEDED",
        "expected_rule": "automotive_collision:incomplete_callback_needed",
        "expected_flag": "missing_claim_number",
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    AUTOMOTIVE_COLLISION_EVAL_CASES,
    ids=lambda case: case["case_id"],
)
async def test_automotive_collision_deterministic_routing_eval(case):
    workflow = BirchwoodCollisionIntakeWorkflow()
    context = WorkflowContext(
        session_id=case["case_id"],
        vertical=AUTOMOTIVE_COLLISION_VERTICAL,
        workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
        workflow_version="v1",
    )
    session = workflow.start_session(context)
    session["channel_metadata"]["scripted_intake"]["fields"] = _fields(
        case.get("fields", {})
    )
    session["channel_metadata"]["scripted_intake"]["completed"] = True
    session["channel_metadata"]["stage"] = "DYNAMIC"

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text=case["dynamic_text"],
            session_state=session,
        ),
    )
    score = _score_automotive_case(result, case)
    deepeval_case = LLMTestCase(
        input=case["dynamic_text"],
        actual_output=result.assistant_text,
        expected_output=f"Route to {case['expected_disposition']}.",
        metadata={"case_id": case["case_id"], "suite": "automotive_collision"},
    )

    assert_deepeval_score(
        deepeval_case,
        score,
        "deterministic_automotive_collision_routing",
    )


@pytest.mark.asyncio
async def test_automotive_collision_language_boundaries_eval():
    workflow = BirchwoodCollisionIntakeWorkflow()
    context = WorkflowContext(
        session_id="birchwood_language_boundaries_001",
        vertical=AUTOMOTIVE_COLLISION_VERTICAL,
        workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
        workflow_version="v1",
    )
    session = workflow.start_session(context)
    session["channel_metadata"]["scripted_intake"]["fields"] = _fields({})
    session["channel_metadata"]["scripted_intake"]["completed"] = True
    session["channel_metadata"]["stage"] = "DYNAMIC"

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text="Can you estimate and approve my claim?", session_state=session
        ),
    )
    text = result.assistant_text.lower()
    forbidden = [
        "repair estimate " + "guaranteed",
        "coverage " + "approved",
        "claim " + "approved",
        "definitely " + "covered",
        "we will " + "pay",
    ]
    passed = all(phrase not in text for phrase in forbidden)
    score = EvalScoreResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        reason="Automotive collision language stayed within intake boundaries.",
        details={"actual_output": result.assistant_text},
    )
    deepeval_case = LLMTestCase(
        input="Can you estimate and approve my claim?",
        actual_output=result.assistant_text,
        expected_output="No repair estimate or insurance approval promise.",
        metadata={"case_id": "birchwood_language_boundaries_001"},
    )

    assert_deepeval_score(
        deepeval_case,
        score,
        "deterministic_automotive_collision_language_boundaries",
    )


def test_automotive_collision_orca_branding_correctness_eval():
    definition = BirchwoodCollisionIntakeWorkflow().get_definition()
    passed = (
        definition.metadata["powered_by"] == "ORCA"
        and definition.metadata["client_target"] == "Birchwood Automotive Group"
        and not definition.display_name.startswith("ORCA -")
    )
    score = EvalScoreResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        reason="ORCA remains the platform and Birchwood remains the target client.",
        details=definition.model_dump(mode="json"),
    )
    deepeval_case = LLMTestCase(
        input="Check ORCA/Birchwood branding.",
        actual_output=str(definition.metadata),
        expected_output="ORCA platform, Birchwood target client.",
        metadata={"case_id": "birchwood_branding_001"},
    )

    assert_deepeval_score(
        deepeval_case,
        score,
        "deterministic_automotive_collision_branding",
    )


def _fields(overrides: dict) -> dict:
    fields = {
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
        "filing_insurance_claim": "yes",
        "claim_number": "CLM-2026-12345",
        "preferred_collision_center": "BIRCHWOOD_COLLISION_LOCATION_1",
    }
    fields.update(overrides)
    return fields


def _score_automotive_case(result, case: dict) -> EvalScoreResult:
    record = result.updated_state["channel_metadata"]["workflow_final_result"][
        "structured_output"
    ]["intake_record"]
    expected_flag = case.get("expected_flag")
    flag_passed = expected_flag is None or expected_flag in record["flags"]
    passed = (
        result.should_finalize
        and result.recommended_disposition == case["expected_disposition"]
        and case["expected_rule"] in result.rules_triggered
        and flag_passed
    )
    return EvalScoreResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        reason=(
            "Automotive collision deterministic routing matched expectation."
            if passed
            else "Automotive collision deterministic routing did not match expectation."
        ),
        details={
            "expected_disposition": case["expected_disposition"],
            "actual_disposition": result.recommended_disposition,
            "expected_rule": case["expected_rule"],
            "actual_rules": result.rules_triggered,
            "expected_flag": expected_flag,
            "actual_flags": record["flags"],
        },
    )
