"""DeepEval-compatible deterministic healthcare eval tests."""

import sys

from deepeval import assert_test, log_hyperparameters
from deepeval.prompt import Prompt
from deepeval.test_case import LLMTestCase

from src.evals.deepeval_metrics import DeterministicHealthcareMetric
from src.evals.healthcare_eval_scorers import EvalScoreResult

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

_HYPERPARAMETERS_LOGGED = False


def assert_deepeval_score(
    test_case: LLMTestCase,
    score: EvalScoreResult,
    metric_name: str,
) -> None:
    """Register a deterministic scorer with DeepEval and assert the result."""

    _log_deepeval_hyperparameters_once()
    assert_test(
        test_case,
        metrics=[DeterministicHealthcareMetric(metric_name, score)],
        run_async=False,
    )
    assert score.passed, score.model_dump_json(indent=2)


def _log_deepeval_hyperparameters_once() -> None:
    global _HYPERPARAMETERS_LOGGED
    if _HYPERPARAMETERS_LOGGED:
        return

    offline_prompt = Prompt(
        alias="offline_healthcare_eval_adapter",
        text_template=(
            "Deterministic offline healthcare eval adapter. No live LLM prompt is used."
        ),
    )
    offline_prompt.hash = "offline-deterministic"
    offline_prompt.version = "phase-12-5"
    log_hyperparameters(
        lambda: {
            "eval_mode": "offline",
            "llm_provider": "deterministic_mock",
            "live_llm_enabled": "false",
            "prompt": offline_prompt,
        }
    )
    _HYPERPARAMETERS_LOGGED = True
