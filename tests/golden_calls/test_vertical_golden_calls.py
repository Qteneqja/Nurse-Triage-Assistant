"""Assertions for the vertical golden-call pack (Birchwood + insurance).

Deterministic only — exercises production scripted-field parsing and the
rules engines. The healthcare golden-call suite is separate and untouched.
"""

import json
from pathlib import Path

import pytest

from tests.golden_calls.vertical_runner import (
    VERTICAL_CASES_DIR,
    load_vertical_cases,
    run_vertical_case,
)

_CASE_FILES = sorted(VERTICAL_CASES_DIR.rglob("*.json"))


def test_vertical_cases_load_and_validate():
    cases = load_vertical_cases()
    birchwood = [c for c in cases if c["vertical"] == "automotive_collision"]
    insurance = [c for c in cases if c["vertical"] == "insurance"]
    # Birchwood is the pilot target — it must dominate the pack.
    assert len(birchwood) >= 8
    assert len(insurance) >= 2


@pytest.mark.parametrize("case_file", _CASE_FILES, ids=lambda p: p.stem)
def test_vertical_golden_call(case_file: Path):
    case = json.loads(case_file.read_text(encoding="utf-8"))
    result = run_vertical_case(case)

    assert result.error is None, f"{case['case_id']} raised: {result.error}"

    expected_outcome = case["expected_outcome"]
    allowed = (
        expected_outcome if isinstance(expected_outcome, list) else [expected_outcome]
    )
    assert result.outcome in allowed, (
        f"{case['case_id']}: outcome {result.outcome!r} not in {allowed!r}\n"
        f"raw: {json.dumps(result.raw, indent=1)[:1200]}"
    )

    for flag in case.get("expected_flags_contain", []):
        assert flag in result.flags, (
            f"{case['case_id']}: expected flag {flag!r} missing from {result.flags!r}"
        )
    for flag in case.get("expected_flags_absent", []):
        assert flag not in result.flags, (
            f"{case['case_id']}: forbidden flag {flag!r} present in {result.flags!r}"
        )
    for rule in case.get("expected_rules_contain", []):
        assert rule in result.rules_triggered, (
            f"{case['case_id']}: expected rule {rule!r} missing from "
            f"{result.rules_triggered!r}"
        )
    if "expected_human_review" in case:
        assert result.human_review_required == case["expected_human_review"], (
            f"{case['case_id']}: human_review_required="
            f"{result.human_review_required}, expected "
            f"{case['expected_human_review']}"
        )
    for item in case.get("expected_missing_contains", []):
        assert item in result.missing_information, (
            f"{case['case_id']}: expected {item!r} in missing_information "
            f"{result.missing_information!r}"
        )
    if "expected_injuries_mentioned" in case:
        assert result.injuries_mentioned == case["expected_injuries_mentioned"], (
            f"{case['case_id']}: injuries_mentioned={result.injuries_mentioned}, "
            f"expected {case['expected_injuries_mentioned']}"
        )
