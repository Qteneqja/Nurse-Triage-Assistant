"""
Golden-Call Regression Tests
Purpose: Automated regression suite detecting when triage outputs drift
         from expected clinical decisions. These are synthetic software
         regression test cases, not clinical advice.
Date Created: 2026-03-02
Step: Production Hardening — Step 3E

Modes:
  - deterministic_only (default, CI): Tests safety gate + rules only. LLM blocked.
  - full: Tests complete pipeline including LLM (manual only).

Run in CI:
  GOLDEN_CALL_MODE=deterministic_only DISABLE_EXTERNAL_CALLS=1 pytest tests/golden_calls/test_golden_calls.py -v
"""
from __future__ import annotations

import json
import logging
import os
import warnings
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from tests.golden_calls.runner import (
    GoldenCallResult,
    get_mode,
    install_llm_block,
    load_all_cases,
    run_case_deterministic,
    validate_case_against_schema,
)

logger = logging.getLogger(__name__)

CASES_DIR = Path(__file__).parent / "cases"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def golden_cases() -> List[dict]:
    """Load all golden-call case files, validated against schema."""
    return load_all_cases()


@pytest.fixture(scope="module")
def llm_block_patches():
    """Install LLM blocking patches for deterministic_only mode."""
    mode = get_mode()
    if mode == "deterministic_only":
        patches = install_llm_block()
        started = [p.start() for p in patches]
        yield started
        for p in patches:
            try:
                p.stop()
            except RuntimeError:
                pass
    else:
        yield []


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def _format_failure_detail(case: dict, result: GoldenCallResult) -> str:
    """Format a detailed failure message with expected vs actual for every field."""
    expected_disp = case["expected_disposition"]
    return (
        f"\n{'=' * 70}\n"
        f"GOLDEN CALL FAILURE: {case['case_id']}\n"
        f"Description: {case['description']}\n"
        f"Severity: {case['severity']}\n"
        f"{'=' * 70}\n"
        f"EXPECTED:\n"
        f"  Disposition:  {expected_disp}\n"
        f"  Escalation:   {case['expected_escalation_required']}\n"
        f"  Red Flags:    {case['expected_red_flags_triggered']}\n"
        f"  Rules:        {case['expected_rules_triggered']}\n"
        f"  Confidence:   {case['confidence_range']}\n"
        f"\n"
        f"ACTUAL:\n"
        f"  Disposition:  {result.disposition}\n"
        f"  Escalation:   {result.escalation_required}\n"
        f"  Red Flags:    {result.red_flags_triggered}\n"
        f"  Rules:        {result.rules_triggered}\n"
        f"  Confidence:   {result.confidence}\n"
        f"  Error:        {result.error}\n"
        f"\n"
        f"RAW DECISION:\n"
        f"  {json.dumps(result.raw_decision, indent=2, default=str)}\n"
        f"{'=' * 70}\n"
    )


def _check_disposition(case: dict, result: GoldenCallResult) -> List[str]:
    """Check disposition matches expected. Returns list of error messages."""
    expected = case["expected_disposition"]
    actual = result.disposition

    if isinstance(expected, list):
        if actual not in expected:
            return [f"Disposition '{actual}' not in allowed set {expected}"]
    else:
        if actual != expected:
            return [f"Disposition '{actual}' != expected '{expected}'"]
    return []


def _check_escalation(case: dict, result: GoldenCallResult) -> List[str]:
    """Check escalation flag matches."""
    if result.escalation_required != case["expected_escalation_required"]:
        return [
            f"Escalation {result.escalation_required} != expected {case['expected_escalation_required']}"
        ]
    return []


def _check_red_flags(case: dict, result: GoldenCallResult) -> List[str]:
    """Check red flags triggered.

    All expected flags must be present (order-insensitive).
    For critical/high: unexpected extra flags FAIL.
    For medium/low: unexpected extra flags generate WARNINGS only.
    """
    errors = []
    expected_set = set(case["expected_red_flags_triggered"])
    actual_set = set(result.red_flags_triggered)

    # Check all expected flags are present
    missing = expected_set - actual_set
    if missing:
        errors.append(f"Missing expected red flags: {sorted(missing)}")

    # Check for unexpected extra flags
    extra = actual_set - expected_set
    if extra:
        severity = case["severity"]
        if severity in ("critical", "high"):
            errors.append(f"Unexpected extra red flags (strict): {sorted(extra)}")
        else:
            warnings.warn(
                f"[{case['case_id']}] Unexpected extra red flags (warning only): {sorted(extra)}",
                UserWarning,
            )

    return errors


def _check_rules(case: dict, result: GoldenCallResult) -> List[str]:
    """Check rules triggered.

    Same logic as red flags: expected must be present,
    extra rules fail for critical/high, warn for medium/low.
    """
    errors = []
    expected_set = set(case["expected_rules_triggered"])
    actual_set = set(result.rules_triggered)

    missing = expected_set - actual_set
    if missing:
        errors.append(f"Missing expected rules: {sorted(missing)}")

    extra = actual_set - expected_set
    if extra:
        severity = case["severity"]
        if severity in ("critical", "high"):
            errors.append(f"Unexpected extra rules (strict): {sorted(extra)}")
        else:
            warnings.warn(
                f"[{case['case_id']}] Unexpected extra rules (warning only): {sorted(extra)}",
                UserWarning,
            )

    return errors


def _check_confidence(case: dict, result: GoldenCallResult) -> List[str]:
    """Check confidence is within expected range (inclusive)."""
    lo, hi = case["confidence_range"]
    if not (lo <= result.confidence <= hi):
        return [f"Confidence {result.confidence} not in [{lo}, {hi}]"]
    return []


# ---------------------------------------------------------------------------
# Tests — parametrized over all case files
# ---------------------------------------------------------------------------

def _get_case_ids():
    """Get all case file paths for parametrization."""
    if not CASES_DIR.exists():
        return []
    return sorted(CASES_DIR.glob("case_*.json"))


@pytest.mark.parametrize(
    "case_file",
    _get_case_ids(),
    ids=lambda p: p.stem,
)
def test_golden_call(case_file: Path, llm_block_patches):
    """Run a single golden-call case and assert all fields match expected values."""
    with open(case_file) as f:
        case = json.load(f)

    # Validate case structure
    schema_errors = validate_case_against_schema(case)
    assert not schema_errors, f"Invalid case file {case_file.name}: {schema_errors}"

    # Run deterministic evaluation
    result = run_case_deterministic(case)

    # Check for runner errors
    assert result.error is None, (
        f"Runner error for {case['case_id']}: {result.error}\n"
        + _format_failure_detail(case, result)
    )

    # Collect all assertion failures
    all_errors: List[str] = []
    all_errors.extend(_check_disposition(case, result))
    all_errors.extend(_check_escalation(case, result))
    all_errors.extend(_check_red_flags(case, result))
    all_errors.extend(_check_rules(case, result))
    all_errors.extend(_check_confidence(case, result))

    severity = case["severity"]

    if all_errors:
        detail = _format_failure_detail(case, result)
        error_summary = "\n".join(f"  - {e}" for e in all_errors)

        if severity in ("critical", "high"):
            pytest.fail(
                f"Golden call FAILED [{severity}]: {case['case_id']}\n"
                f"Errors:\n{error_summary}\n{detail}"
            )
        else:
            # medium/low severity — warning only, do not fail suite
            warnings.warn(
                f"Golden call drift [{severity}]: {case['case_id']}\n"
                f"Errors:\n{error_summary}\n{detail}",
                UserWarning,
            )


def test_all_cases_valid_schema():
    """Verify all case files pass JSON schema validation."""
    cases = load_all_cases()
    assert len(cases) >= 5, f"Expected at least 5 golden call cases, found {len(cases)}"


def test_llm_blocked_in_deterministic_mode():
    """Verify that LLM calls are hard-blocked in deterministic_only mode."""
    mode = get_mode()
    if mode != "deterministic_only":
        pytest.skip("Only applies in deterministic_only mode")

    # Attempting to call the LLM should raise immediately
    patches = install_llm_block()
    started = [p.start() for p in patches]
    try:
        from src.llm.client import StructuredLLMClient
        client = StructuredLLMClient.__new__(StructuredLLMClient)

        with pytest.raises(RuntimeError, match="External LLM call attempted"):
            import asyncio
            asyncio.get_event_loop().run_until_complete(client._raw_call([]))
    finally:
        for p in patches:
            try:
                p.stop()
            except RuntimeError:
                pass
