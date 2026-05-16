"""Tiny DeepEval metric bridge for deterministic healthcare scorers."""

from __future__ import annotations

from deepeval.metrics.base_metric import BaseMetric
from deepeval.test_case import LLMTestCase

from src.evals.healthcare_eval_scorers import EvalScoreResult


class DeterministicHealthcareMetric(BaseMetric):
    """DeepEval metric wrapper around a precomputed deterministic score."""

    def __init__(self, name: str, score: EvalScoreResult) -> None:
        self.name = name
        self.threshold = 1.0
        self.score = score.score
        self.reason = score.reason
        self.success = score.passed
        self.error = None
        self.async_mode = False
        self.verbose_mode = False
        self.evaluation_model = "deterministic-healthcare-scorer"
        self._score_result = score

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        del test_case, args, kwargs
        self.score = self._score_result.score
        self.reason = self._score_result.reason
        self.success = self._score_result.passed
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case, *args, **kwargs)

    def is_successful(self) -> bool:
        return bool(self.success)

    @property
    def __name__(self) -> str:
        return self.name
