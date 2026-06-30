"""Conversation golden calls — Birchwood gated-LLM conversational intake.

Replays recorded conversations (caller turns + recorded LLM outputs) through the
PRODUCTION conversational workflow with the LLM mocked — deterministic, no
external LLM, just like the other golden-call packs. Asserts the deterministic
disposition, captured fields, flags, the injury safety net, and LLM-call counts.
"""

import json
from pathlib import Path

import pytest

import src.config as config
from tests.golden_calls.conversation_runner import (
    CONVERSATION_CASES_DIR,
    load_conversation_cases,
    run_conversation_case,
)

_CASE_FILES = sorted(CONVERSATION_CASES_DIR.rglob("*.json"))


def test_conversation_cases_load_and_validate():
    cases = load_conversation_cases()
    assert len(cases) >= 5
    assert all(c["turns"] for c in cases)


@pytest.mark.parametrize("case_file", _CASE_FILES, ids=lambda p: p.stem)
def test_conversation_golden_call(case_file: Path, monkeypatch):
    monkeypatch.setattr(config, "BIRCHWOOD_CONVERSATIONAL_INTAKE", True)
    case = json.loads(case_file.read_text(encoding="utf-8"))
    result = run_conversation_case(case)

    assert result.error is None, f"{case['case_id']} raised: {result.error}"
    assert result.finalized == case["expected_finalized"], (
        f"{case['case_id']}: finalized={result.finalized}, "
        f"expected {case['expected_finalized']}"
    )

    allowed = case["expected_disposition"]
    allowed = allowed if isinstance(allowed, list) else [allowed]
    assert result.disposition in allowed, (
        f"{case['case_id']}: disposition {result.disposition!r} not in {allowed!r}"
    )

    for key, value in case.get("expected_fields_contain", {}).items():
        assert result.fields.get(key) == value, (
            f"{case['case_id']}: field {key}={result.fields.get(key)!r}, "
            f"expected {value!r}"
        )

    for flag in case.get("expected_flags_contain", []):
        assert flag in result.flags, (
            f"{case['case_id']}: flag {flag!r} missing from {result.flags!r}"
        )

    if case.get("expected_advisory_spoken"):
        from src.safety.injury_detection import INJURY_SAFETY_ADVISORY

        assert any(INJURY_SAFETY_ADVISORY in t for t in result.assistant_texts), (
            f"{case['case_id']}: injury safety advisory was not spoken"
        )

    if "expected_llm_calls" in case:
        assert result.llm_call_count == case["expected_llm_calls"], (
            f"{case['case_id']}: {result.llm_call_count} LLM calls, "
            f"expected {case['expected_llm_calls']}"
        )
