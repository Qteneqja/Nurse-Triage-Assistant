"""Ready-to-paste Codex prompt generation for healthcare eval failures."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from src.evals.failure_classifier import (
    RERUN_COMMANDS,
    SAFETY_CONSTRAINTS,
    classify_failure,
)


REPO_URL = "https://github.com/Qteneqja/Nurse-Triage-Assistant"


def generate_codex_fix_prompt(
    failures: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
) -> str:
    """Build a safe, specific prompt for a human-approved Codex fix session."""

    summary_data = summary or {}
    sample_only = bool(summary_data.get("sample_only"))
    branch = summary_data.get("branch") or "unknown"
    commit = summary_data.get("commit") or "unknown"
    classified = [_failure_prompt_data(failure) for failure in failures]
    likely_files = _dedupe(
        file for failure in classified for file in failure["likely_files"]
    )
    rerun_commands = _dedupe(
        command for failure in classified for command in failure["rerun_commands"]
    ) or list(RERUN_COMMANDS)

    lines = [
        "You are working in this repo:",
        "",
        REPO_URL,
        "",
        f"Current branch: {branch}",
        f"Current commit: {commit}",
        "",
        "This is a healthcare/triage safety project.",
        "This prompt is generated from deterministic eval failures.",
        "This is autonomous QA guidance, not autonomous clinical self-training.",
        "",
    ]
    if sample_only:
        lines.extend(
            [
                "SAMPLE ONLY \u2014 NOT A REAL EVAL FAILURE",
                "Use this only to preview the failure-reporting workflow.",
                "",
            ]
        )

    lines.extend(["Eval failure:", ""])
    if not classified:
        lines.extend(
            [
                "No failed eval cases were supplied. Confirm the input report before changing code.",
                "",
            ]
        )
    for failure in classified:
        lines.extend(
            [
                f"- case_id: {failure['case_id']}",
                f"- suite: {failure['suite']}",
                f"- category: {failure['category']}",
                f"- severity: {failure['severity']}",
                f"- failed scorer: {failure['failed_scorer']}",
                f"- expected behavior: {failure['expected_behavior']}",
                f"- observed behavior: {failure['observed_behavior']}",
                f"- reason: {failure['reason']}",
                "- details:",
                _indent(_details_json(failure["details"]), "  "),
                "",
            ]
        )

    lines.extend(
        [
            "Required investigation:",
            "",
            "Inspect the likely root-cause files first:",
        ]
    )
    lines.extend(f"- {path}" for path in likely_files)
    lines.extend(["", "Safety constraints:", ""])
    lines.extend(f"- {constraint}" for constraint in SAFETY_CONSTRAINTS)
    lines.extend(
        [
            "",
            "Task:",
            "",
            "Fix the root cause without weakening healthcare safety gates.",
            "Avoid clinical logic changes unless they are directly necessary to restore the documented safety behavior.",
            "Do not lower red-flag escalation thresholds.",
            "Do not bypass healthcare completeness or minimum-turn gates.",
            "Add or update regression tests for any bug fixed.",
            "",
            "Validation:",
            "",
            "Run:",
            "",
            "```bash",
        ]
    )
    lines.extend(rerun_commands)
    lines.extend(
        [
            "```",
            "",
            "Final response:",
            "",
            "List files changed, root cause, tests run, and any limitations.",
            "Clearly state whether clinical triage behavior changed.",
        ]
    )
    return "\n".join(lines) + "\n"


def _failure_prompt_data(failure: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    data = _as_dict(failure)
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    classification = classify_failure(data)
    return {
        "case_id": data.get("case_id") or "unknown",
        "suite": data.get("suite") or "unknown",
        "category": data.get("category") or classification.category,
        "severity": data.get("severity") or classification.severity,
        "failed_scorer": (
            data.get("failed_scorer")
            or data.get("scorer_name")
            or details.get("failed_scorer")
            or details.get("scorer_name")
            or classification.failed_scorer
        ),
        "expected_behavior": (
            data.get("expected_behavior")
            or details.get("expected_behavior")
            or classification.expected_behavior
        ),
        "observed_behavior": (
            data.get("observed_behavior")
            or details.get("observed_behavior")
            or data.get("reason")
            or "Observed behavior was not provided."
        ),
        "reason": data.get("reason") or "No scorer reason was provided.",
        "details": details,
        "likely_files": data.get("likely_files") or classification.likely_files,
        "rerun_commands": data.get("rerun_commands") or classification.rerun_commands,
    }


def _details_json(details: dict[str, Any]) -> str:
    try:
        rendered = json.dumps(details, indent=2, sort_keys=True)
    except TypeError:
        rendered = json.dumps(_json_safe(details), indent=2, sort_keys=True)
    if len(rendered) > 3000:
        return rendered[:3000] + "\n... truncated for prompt readability ..."
    return rendered


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _as_dict(value: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" for line in text.splitlines())


def _dedupe(values: Any) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen
