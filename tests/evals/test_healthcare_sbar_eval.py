from deepeval.test_case import LLMTestCase

from src.evals.healthcare_eval_scorers import score_sbar_completeness
from src.evals.simulated_patient_runner import run_simulated_patient_case
from src.evals.triage_eval_adapter import EvalRunResult
from tests.evals import assert_deepeval_score


def test_completed_non_emergency_healthcare_call_produces_sbar():
    case = {
        "case_id": "sbar_completed_non_emergency_001",
        "chief_complaint": "mild cough",
        "demographics": {"age": 36, "sex": "female"},
        "scripted_answers": [
            "I have a mild cough.",
            "It started two days ago.",
            "It is mild.",
            "No fever and my breathing is okay.",
            "I took Tylenol earlier.",
            "No known allergies.",
            "No other symptoms.",
        ],
        "expected": {"final_disposition": "SELF_CARE"},
    }
    result = run_simulated_patient_case(case)
    deepeval_case = LLMTestCase(
        input="\n".join(case["scripted_answers"]),
        actual_output=result.raw_session["finalize_output"]["sbar_report"],
        expected_output="Complete SBAR structure.",
        metadata={"case_id": case["case_id"], "suite": "sbar"},
    )

    assert deepeval_case.actual_output
    assert result.finalization_reason == "sufficient_information"

    score = score_sbar_completeness(result)
    assert_deepeval_score(
        deepeval_case,
        score,
        "deterministic_sbar_completeness",
    )


def test_escalated_healthcare_call_produces_handoff_sbar():
    case = {
        "case_id": "sbar_escalated_red_flag_001",
        "chief_complaint": "breathing problem",
        "demographics": {"age": 58, "sex": "male"},
        "scripted_answers": [
            "I can't breathe at all.",
        ],
        "expected": {"final_disposition": "ER_NOW"},
    }
    result = run_simulated_patient_case(case)
    deepeval_case = LLMTestCase(
        input=case["scripted_answers"][0],
        actual_output=result.raw_session["finalize_output"]["sbar_report"],
        expected_output="Emergency handoff SBAR structure.",
        metadata={"case_id": case["case_id"], "suite": "sbar"},
    )

    score = score_sbar_completeness(result)
    assert_deepeval_score(
        deepeval_case,
        score,
        "deterministic_sbar_completeness",
    )
    assert result.escalation_required is True
    assert result.sbar_fields["situation"]
    assert result.sbar_fields["recommendation"]


def test_missing_sbar_fields_fail_the_eval():
    result = EvalRunResult(
        case_id="sbar_missing_fields_synthetic_001",
        transcript=[],
        assistant_messages=[],
        is_finalized=True,
        final_disposition="HUMAN_REVIEW",
        escalation_required=True,
        finalization_reason="sufficient_information",
        sbar_fields={"situation": "Caller reported symptoms."},
        sbar_available=False,
    )

    score = score_sbar_completeness(result)
    assert score.passed is False
    assert "background" in score.details["missing_sbar_fields"]
