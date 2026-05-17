from deepeval.test_case import LLMTestCase
import pytest

from src.evals.healthcare_eval_scorers import EvalScoreResult
from src.platform.workflows.schemas import WorkflowContext, WorkflowInput
from src.verticals.insurance.constants import (
    INSURANCE_CLAIMS_FNOL_WORKFLOW_ID,
    INSURANCE_VERTICAL,
)
from src.verticals.insurance.workflow import InsuranceClaimsFnolWorkflow
from tests.evals import assert_deepeval_score


INSURANCE_FNOL_EVAL_CASES = [
    {
        "case_id": "insurance_fnol_active_fire_001",
        "claim_type": "property damage",
        "incident_summary": "There is visible smoke and damage at the property.",
        "dynamic_text": "There is an active fire right now and the scene is unsafe.",
        "expected_disposition": "EMERGENCY_SERVICES_NOW",
        "expected_rule": "insurance:emergency:active_fire",
    },
    {
        "case_id": "insurance_fnol_standard_roof_damage_001",
        "claim_type": "property damage",
        "incident_summary": "A fallen branch damaged part of the roof yesterday.",
        "dynamic_text": (
            "No one is hurt, no emergency services were needed, "
            "the property is safe now, and I have photos ready."
        ),
        "expected_disposition": "STANDARD_CLAIM_INTAKE",
        "expected_rule": "insurance:standard_claim_intake",
    },
    {
        "case_id": "insurance_fnol_missing_theft_info_001",
        "claim_type": "theft/loss",
        "policy_number": "",
        "loss_datetime": "",
        "loss_location": "",
        "incident_summary": "Items are missing but I do not know when it happened.",
        "dynamic_text": (
            "I am not sure when it happened, I do not have receipts, "
            "and I have not filed a report yet."
        ),
        "expected_disposition": "DOCUMENTS_NEEDED",
        "expected_rule": "insurance:documents_needed",
    },
    {
        "case_id": "insurance_fnol_information_only_001",
        "claim_type": "other",
        "incident_summary": "The caller only wants to know the claims process.",
        "dynamic_text": (
            "I just want to understand the process and I do not want to start "
            "a claim right now."
        ),
        "expected_disposition": "INFORMATION_ONLY",
        "expected_rule": "insurance:information_only",
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    INSURANCE_FNOL_EVAL_CASES,
    ids=lambda case: case["case_id"],
)
async def test_insurance_fnol_deterministic_routing_eval(case):
    workflow = InsuranceClaimsFnolWorkflow()
    context = WorkflowContext(
        session_id=case["case_id"],
        vertical=INSURANCE_VERTICAL,
        workflow_id=INSURANCE_CLAIMS_FNOL_WORKFLOW_ID,
        workflow_version="v1",
    )
    session = workflow.start_session(context)
    session["channel_metadata"]["scripted_intake"]["fields"] = _fields(case)
    session["channel_metadata"]["scripted_intake"]["completed"] = True
    session["channel_metadata"]["stage"] = "DYNAMIC"

    result = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text=case["dynamic_text"],
            session_state=session,
        ),
    )
    score = _score_insurance_case(result, case)
    deepeval_case = LLMTestCase(
        input=case["dynamic_text"],
        actual_output=result.assistant_text,
        expected_output=f"Route to {case['expected_disposition']}.",
        metadata={"case_id": case["case_id"], "suite": "insurance_fnol"},
    )

    assert_deepeval_score(
        deepeval_case,
        score,
        "deterministic_insurance_fnol_routing",
    )


def _fields(case: dict) -> dict[str, str]:
    return {
        "caller_name": "Jordan Smith",
        "callback_number": "+15551230000",
        "policy_number": case.get("policy_number", "POL-4421"),
        "claim_type": case["claim_type"],
        "loss_datetime": case.get("loss_datetime", "2026-05-15 08:30"),
        "loss_location": case.get("loss_location", "123 Main Street, Winnipeg"),
        "incident_summary": case["incident_summary"],
    }


def _score_insurance_case(result, case: dict) -> EvalScoreResult:
    passed = (
        result.should_finalize
        and result.recommended_disposition == case["expected_disposition"]
        and case["expected_rule"] in result.rules_triggered
    )
    return EvalScoreResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        reason=(
            "Insurance FNOL deterministic routing matched expectation."
            if passed
            else "Insurance FNOL deterministic routing did not match expectation."
        ),
        details={
            "expected_disposition": case["expected_disposition"],
            "actual_disposition": result.recommended_disposition,
            "expected_rule": case["expected_rule"],
            "actual_rules": result.rules_triggered,
            "should_finalize": result.should_finalize,
            "escalation_required": result.escalation_required,
        },
    )
