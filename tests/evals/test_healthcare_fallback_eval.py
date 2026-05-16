from deepeval.test_case import LLMTestCase

from src.evals.healthcare_eval_scorers import score_invalid_output_fails_closed
from src.evals.simulated_patient_runner import run_simulated_patient_case
from tests.evals import assert_deepeval_score


def test_malformed_llm_output_fails_closed_to_human_review():
    case = {
        "case_id": "fallback_malformed_llm_output_001",
        "chief_complaint": "mild cough",
        "demographics": {"age": 39, "sex": "female"},
        "scripted_answers": [
            "I have a mild cough.",
        ],
        "force_malformed_llm_output": True,
        "expected": {"final_disposition": "HUMAN_REVIEW"},
    }
    result = run_simulated_patient_case(case)
    deepeval_case = LLMTestCase(
        input=case["scripted_answers"][0],
        actual_output="\n".join(result.assistant_messages),
        expected_output="Safe fail-closed review path for invalid structured output.",
        metadata={"case_id": case["case_id"], "suite": "fallback"},
    )

    assert deepeval_case.actual_output

    score = score_invalid_output_fails_closed(result)
    assert_deepeval_score(
        deepeval_case,
        score,
        "deterministic_invalid_output_fails_closed",
    )
    assert result.final_disposition != "SELF_CARE"
    assert result.finalization_reason == "llm_validation_failure"
