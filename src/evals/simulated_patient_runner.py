"""Deterministic scripted-patient runner for eval tests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from src.evals.triage_eval_adapter import (
    EvalRunResult,
    TriageEvalAdapter,
    TriageEvalCase,
)


async def run_simulated_patient_case_async(
    case: TriageEvalCase | dict[str, Any],
) -> EvalRunResult:
    """Run one scripted patient case through the offline triage adapter."""

    return await TriageEvalAdapter().run_case(case)


def run_simulated_patient_case(
    case: TriageEvalCase | dict[str, Any],
) -> EvalRunResult:
    """Synchronous wrapper for pytest/DeepEval test files."""

    return asyncio.run(run_simulated_patient_case_async(case))


async def run_simulated_patient_cases_async(
    cases: Iterable[TriageEvalCase | dict[str, Any]],
) -> list[EvalRunResult]:
    """Run many scripted cases sequentially for deterministic ordering."""

    results: list[EvalRunResult] = []
    adapter = TriageEvalAdapter()
    for case in cases:
        results.append(await adapter.run_case(case))
    return results


def run_simulated_patient_cases(
    cases: Iterable[TriageEvalCase | dict[str, Any]],
) -> list[EvalRunResult]:
    """Synchronous multi-case runner."""

    return asyncio.run(run_simulated_patient_cases_async(cases))
