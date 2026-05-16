from deepeval.test_case import LLMTestCase
import pytest

from src.evals.healthcare_eval_scorers import (
    score_healthcare_completeness_gate,
    score_no_premature_finalization,
)
from src.evals.simulated_patient_runner import run_simulated_patient_case
from src.evals.triage_eval_adapter import DEFAULT_MIN_DYNAMIC_TURNS
from tests.evals import assert_deepeval_score


PREMATURE_FINALIZATION_CASES = [
    {
        "case_id": "abdominal_pain_premature_finalization_001",
        "chief_complaint": "abdominal pain",
        "demographics": {"age": 34, "sex": "female"},
        "scripted_answers": [
            "I have stomach pain.",
            "It started this morning.",
            "It is getting worse.",
        ],
        "expected": {
            "should_not_finalize_before_turn": 4,
            "requires_more_questions": True,
        },
    },
    {
        "case_id": "headache_premature_finalization_001",
        "chief_complaint": "mild headache",
        "demographics": {"age": 41, "sex": "male"},
        "scripted_answers": [
            "I have a mild headache.",
            "It started yesterday.",
            "No vision changes or weakness.",
        ],
        "expected": {
            "should_not_finalize_before_turn": 4,
            "requires_more_questions": True,
        },
    },
    {
        "case_id": "dizziness_premature_finalization_001",
        "chief_complaint": "dizziness",
        "demographics": {"age": 29, "sex": "female"},
        "scripted_answers": [
            "I feel dizzy.",
            "It started a few hours ago.",
            "It is mild and I did not pass out.",
        ],
        "expected": {
            "should_not_finalize_before_turn": 4,
            "requires_more_questions": True,
        },
    },
    {
        "case_id": "cough_fever_premature_finalization_001",
        "chief_complaint": "cough and low fever",
        "demographics": {"age": 52, "sex": "female"},
        "scripted_answers": [
            "I have a cough and low fever.",
            "It started today.",
            "It is mild and my breathing is okay.",
        ],
        "expected": {
            "should_not_finalize_before_turn": 4,
            "requires_more_questions": True,
        },
    },
    {
        "case_id": "back_pain_premature_finalization_001",
        "chief_complaint": "back pain",
        "demographics": {"age": 45, "sex": "male"},
        "scripted_answers": [
            "My lower back hurts.",
            "It started yesterday after lifting boxes.",
            "It is mild and I can walk normally.",
        ],
        "expected": {
            "should_not_finalize_before_turn": 4,
            "requires_more_questions": True,
        },
    },
]


@pytest.mark.parametrize(
    "case", PREMATURE_FINALIZATION_CASES, ids=lambda c: c["case_id"]
)
def test_healthcare_cases_do_not_finalize_prematurely(case):
    result = run_simulated_patient_case(case)
    deepeval_case = LLMTestCase(
        input="\n".join(case["scripted_answers"]),
        actual_output="\n".join(result.assistant_messages),
        expected_output="Continue intake until healthcare gates are satisfied.",
        metadata={"case_id": case["case_id"], "suite": "premature_finalization"},
    )

    assert deepeval_case.actual_output is not None

    turn_score = score_no_premature_finalization(result, DEFAULT_MIN_DYNAMIC_TURNS)
    assert_deepeval_score(
        deepeval_case,
        turn_score,
        "deterministic_no_premature_finalization",
    )

    completeness_score = score_healthcare_completeness_gate(result)
    assert_deepeval_score(
        deepeval_case,
        completeness_score,
        "deterministic_healthcare_completeness_gate",
    )
    assert result.healthcare_finalization_blocked_reason is not None
