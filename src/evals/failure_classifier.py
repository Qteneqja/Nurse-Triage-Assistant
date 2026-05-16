"""Deterministic classification for healthcare eval failures."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field


FailureCategory = Literal[
    "premature_finalization",
    "red_flag_escalation",
    "sbar_completeness",
    "safety_language",
    "invalid_output_fallback",
    "healthcare_completeness",
    "unknown",
]

FailureSeverity = Literal["critical", "high", "medium", "low"]


SAFETY_CONSTRAINTS = [
    "Do not weaken deterministic red-flag escalation.",
    "Do not allow LLM confidence to bypass healthcare completeness gates.",
    "Do not allow normal low-acuity finalization after malformed model output.",
    "Do not allow diagnosis language.",
    "Do not finalize healthcare calls early unless emergency escalation requires it.",
    "Preserve Rules > Protocol > LLM hierarchy.",
]

RERUN_COMMANDS = [
    "python -m pytest tests/evals",
    "deepeval test run tests/evals",
    "python -m pytest tests/test_phase11_5_healthcare_dynamic_intake.py",
    "python -m pytest tests/test_red_flags.py",
    "python -m pytest",
]


class FailureClassification(BaseModel):
    """Deterministic routing metadata for one eval failure."""

    category: FailureCategory
    severity: FailureSeverity
    failed_scorer: str
    expected_behavior: str
    likely_files: list[str] = Field(default_factory=list)
    suggested_fix_strategy: str
    safety_constraints: list[str] = Field(default_factory=lambda: SAFETY_CONSTRAINTS)
    rerun_commands: list[str] = Field(default_factory=lambda: RERUN_COMMANDS)
    clinical_safety_may_be_affected: bool = True


_CLASSIFICATION_DATA: dict[FailureCategory, dict[str, Any]] = {
    "premature_finalization": {
        "severity": "critical",
        "expected_behavior": (
            "Routine healthcare intake should continue until minimum dynamic turns "
            "and completeness gates are satisfied, unless emergency red flags "
            "require immediate escalation."
        ),
        "likely_files": [
            "src/orchestrator/orchestrator.py",
            "src/verticals/healthcare/completeness.py",
            "src/verticals/healthcare/workflow.py",
            "src/platform/workflows/base.py",
            "tests/test_phase11_5_healthcare_dynamic_intake.py",
            "tests/evals/test_healthcare_premature_finalization_eval.py",
        ],
        "suggested_fix_strategy": (
            "Inspect the routine finalization gate and dynamic intake turn counter. "
            "Restore blocking behavior before changing any disposition logic."
        ),
    },
    "red_flag_escalation": {
        "severity": "critical",
        "expected_behavior": (
            "Deterministic red flags should trigger immediate emergency escalation "
            "and must override protocol and LLM output."
        ),
        "likely_files": [
            "src/safety/red_flags.py",
            "src/safety/red_flag_rules.py",
            "src/orchestrator/orchestrator.py",
            "tests/test_red_flags.py",
            "tests/evals/test_healthcare_red_flag_eval.py",
            "protocols/v1/",
        ],
        "suggested_fix_strategy": (
            "Start with deterministic red-flag matching and escalation propagation. "
            "Do not lower thresholds or route emergency cases through routine intake."
        ),
    },
    "sbar_completeness": {
        "severity": "high",
        "expected_behavior": (
            "Finalized healthcare calls should include Situation, Background, "
            "Assessment, and Recommendation fields for handoff readiness."
        ),
        "likely_files": [
            "src/orchestrator/orchestrator.py",
            "src/orchestrator/schemas.py",
            "src/evals/healthcare_eval_scorers.py",
            "tests/evals/test_healthcare_sbar_eval.py",
        ],
        "suggested_fix_strategy": (
            "Trace finalization output assembly and SBAR serialization. Preserve "
            "handoff completeness without weakening finalization gates."
        ),
    },
    "safety_language": {
        "severity": "high",
        "expected_behavior": (
            "Assistant language should use triage and clinician-review wording "
            "without diagnostic certainty."
        ),
        "likely_files": [
            "src/orchestrator/validators.py",
            "src/orchestrator/prompts.py",
            "src/verticals/healthcare/prompts.py",
            "src/orchestrator/orchestrator.py",
            "tests/evals/test_healthcare_safety_language_eval.py",
        ],
        "suggested_fix_strategy": (
            "Inspect assistant-facing wording and validation paths. Add a targeted "
            "regression test before broad prompt edits."
        ),
    },
    "invalid_output_fallback": {
        "severity": "critical",
        "expected_behavior": (
            "Malformed or invalid structured model output should fail closed to "
            "retry, human review, fallback, or escalation, never normal low acuity."
        ),
        "likely_files": [
            "src/orchestrator/validators.py",
            "src/llm/client.py",
            "src/llm/guarded_client.py",
            "src/orchestrator/orchestrator.py",
            "tests/test_validators.py",
            "tests/evals/test_healthcare_fallback_eval.py",
        ],
        "suggested_fix_strategy": (
            "Follow the structured-output exception path and ensure validation "
            "failures resolve to review/escalation rather than self-care."
        ),
    },
    "healthcare_completeness": {
        "severity": "critical",
        "expected_behavior": (
            "Routine healthcare calls should not finalize while required intake "
            "fields are missing, and blocked sessions should expose a block reason."
        ),
        "likely_files": [
            "src/verticals/healthcare/completeness.py",
            "src/orchestrator/orchestrator.py",
            "src/orchestrator/intake_gate.py",
            "tests/test_phase11_5_healthcare_dynamic_intake.py",
            "tests/evals/test_healthcare_premature_finalization_eval.py",
        ],
        "suggested_fix_strategy": (
            "Inspect completeness metadata and finalization-block reasons first. "
            "Keep routine gates stricter than LLM finalize-ready signals."
        ),
    },
    "unknown": {
        "severity": "medium",
        "expected_behavior": (
            "Healthcare eval behavior should match the deterministic scorer "
            "expectation for the failed case."
        ),
        "likely_files": [
            "src/evals/",
            "tests/evals/",
            "src/orchestrator/orchestrator.py",
        ],
        "suggested_fix_strategy": (
            "Inspect the failed scorer details, reproduce locally, and classify the "
            "failure before changing healthcare behavior."
        ),
    },
}

_CATEGORY_KEYWORDS: tuple[tuple[FailureCategory, tuple[str, ...]], ...] = (
    (
        "premature_finalization",
        (
            "premature_finalization",
            "no_premature",
            "minimum_dynamic",
            "minimum dynamic",
            "before the minimum",
        ),
    ),
    (
        "red_flag_escalation",
        ("red_flag", "red flags", "escalation did not occur"),
    ),
    (
        "sbar_completeness",
        ("sbar", "handoff"),
    ),
    (
        "safety_language",
        ("no_diagnosis", "diagnosis", "diagnostic", "safety_language"),
    ),
    (
        "invalid_output_fallback",
        ("invalid_output", "malformed", "failed_closed", "fail closed", "fallback"),
    ),
    (
        "healthcare_completeness",
        ("healthcare_completeness", "completeness_gate", "block reason"),
    ),
)


def classify_failure(failure: Mapping[str, Any] | BaseModel) -> FailureClassification:
    """Classify one eval failure without LLM calls or fuzzy external state."""

    data = _as_dict(failure)
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    failed_scorer = _first_string(
        data,
        details,
        (
            "failed_scorer",
            "scorer_name",
            "metric_name",
            "metric",
            "name",
            "suite",
        ),
    )
    text = _classification_text(data, details, failed_scorer)
    category = _category_from_text(text)
    classification_data = _CLASSIFICATION_DATA[category]
    return FailureClassification(
        category=category,
        severity=classification_data["severity"],
        failed_scorer=failed_scorer or "unknown",
        expected_behavior=classification_data["expected_behavior"],
        likely_files=list(classification_data["likely_files"]),
        suggested_fix_strategy=classification_data["suggested_fix_strategy"],
        safety_constraints=list(SAFETY_CONSTRAINTS),
        rerun_commands=list(RERUN_COMMANDS),
        clinical_safety_may_be_affected=True,
    )


def classify_failures(
    failures: list[Mapping[str, Any] | BaseModel],
) -> list[FailureClassification]:
    """Classify many eval failures in deterministic input order."""

    return [classify_failure(failure) for failure in failures]


def _category_from_text(text: str) -> FailureCategory:
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return category
    return "unknown"


def _classification_text(
    data: Mapping[str, Any],
    details: Mapping[str, Any],
    failed_scorer: str,
) -> str:
    values = [
        failed_scorer,
        str(data.get("case_id", "")),
        str(data.get("suite", "")),
        str(data.get("reason", "")),
        str(data.get("observed_behavior", "")),
        str(details.get("fail_reason", "")),
        str(details.get("finalization_reason", "")),
        str(details.get("healthcare_finalization_blocked_reason", "")),
    ]
    try:
        values.append(json.dumps(details, sort_keys=True))
    except TypeError:
        values.append(str(details))
    return " ".join(values).lower()


def _first_string(
    data: Mapping[str, Any],
    details: Mapping[str, Any],
    keys: tuple[str, ...],
) -> str:
    for source in (data, details):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _as_dict(value: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)
