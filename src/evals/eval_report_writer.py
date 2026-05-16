"""Markdown and JSON report writer for healthcare eval runs."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.evals.codex_prompt_generator import generate_codex_fix_prompt
from src.evals.failure_classifier import (
    RERUN_COMMANDS,
    SAFETY_CONSTRAINTS,
    FailureCategory,
    FailureSeverity,
    classify_failure,
)
from src.evals.healthcare_eval_scorers import EvalScoreResult


class EvalCaseReport(BaseModel):
    """Per-case eval result for report output."""

    case_id: str
    suite: str
    passed: bool
    score: float
    reason: str
    scorer_name: str | None = None
    expected_behavior: str | None = None
    observed_behavior: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EvalReport(BaseModel):
    """Full healthcare eval report payload."""

    timestamp: str
    branch: str | None = None
    commit: str | None = None
    total_cases: int
    passed: int
    failed: int
    xfailed_or_skipped: int = 0
    critical_failures: list[str] = Field(default_factory=list)
    safe_to_merge: bool
    recommended_next_action: str
    cases: list[EvalCaseReport]


class EvalFailureItem(BaseModel):
    """Normalized failed eval case for failure reports and Codex prompts."""

    case_id: str
    suite: str
    category: FailureCategory
    severity: FailureSeverity
    failed_scorer: str
    expected_behavior: str
    observed_behavior: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
    likely_files: list[str] = Field(default_factory=list)
    suggested_fix_strategy: str
    safety_constraints: list[str] = Field(default_factory=lambda: SAFETY_CONSTRAINTS)
    rerun_commands: list[str] = Field(default_factory=lambda: RERUN_COMMANDS)
    clinical_safety_may_be_affected: bool = True


class HealthcareEvalFailureReport(BaseModel):
    """Failure-focused healthcare eval report payload."""

    timestamp: str
    branch: str | None = None
    commit: str | None = None
    total_cases: int
    passed_cases: int
    failed_cases: int
    skipped_or_xfailed_cases: int = 0
    overall_status: str
    safe_to_merge: bool
    sample_only: bool = False
    sample_notice: str | None = None
    critical_failures: list[str] = Field(default_factory=list)
    failures: list[EvalFailureItem] = Field(default_factory=list)
    codex_fix_prompt: str


def build_case_report(
    *,
    case_id: str,
    suite: str,
    score: EvalScoreResult,
    scorer_name: str | None = None,
    expected_behavior: str | None = None,
    observed_behavior: str | None = None,
    critical: bool = False,
) -> EvalCaseReport:
    """Create a report row from a deterministic scorer output."""

    details = dict(score.details)
    details["critical"] = critical
    if scorer_name:
        details.setdefault("scorer_name", scorer_name)
    if expected_behavior:
        details.setdefault("expected_behavior", expected_behavior)
    if observed_behavior:
        details.setdefault("observed_behavior", observed_behavior)
    return EvalCaseReport(
        case_id=case_id,
        suite=suite,
        passed=score.passed,
        score=score.score,
        reason=score.reason,
        scorer_name=scorer_name,
        expected_behavior=expected_behavior,
        observed_behavior=observed_behavior,
        details=details,
    )


def build_report(cases: list[EvalCaseReport]) -> EvalReport:
    """Build a report model from per-case rows."""

    failed_cases = [case for case in cases if not case.passed]
    critical_failures = [
        case.case_id for case in failed_cases if case.details.get("critical")
    ]
    safe_to_merge = not critical_failures and not failed_cases
    return EvalReport(
        timestamp=datetime.now(UTC).isoformat(),
        branch=_git_branch(),
        commit=_git_commit(),
        total_cases=len(cases),
        passed=sum(1 for case in cases if case.passed),
        failed=len(failed_cases),
        critical_failures=critical_failures,
        safe_to_merge=safe_to_merge,
        recommended_next_action=(
            "Safe to merge after normal code review."
            if safe_to_merge
            else "Investigate failed healthcare safety evals before merging."
        ),
        cases=cases,
    )


def write_eval_report(
    cases: list[EvalCaseReport],
    output_dir: str | Path = "eval_reports",
) -> EvalReport:
    """Write latest Markdown and JSON healthcare eval summaries."""

    report = build_report(cases)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "latest-healthcare-eval-results.json"
    markdown_path = output_path / "latest-healthcare-eval-summary.md"

    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def build_failure_report(
    report_or_cases: EvalReport
    | dict[str, Any]
    | list[EvalCaseReport | dict[str, Any]],
    *,
    sample_only: bool = False,
) -> HealthcareEvalFailureReport:
    """Build the failure-focused report from an eval summary or case rows."""

    report = _coerce_eval_report(report_or_cases)
    cases = report.cases if report else _coerce_cases(report_or_cases)
    total_cases = report.total_cases if report else len(cases)
    passed_cases = report.passed if report else sum(1 for case in cases if case.passed)
    skipped = report.xfailed_or_skipped if report else 0
    failures = [_failure_item_from_case(case) for case in cases if not case.passed]
    critical_failures = [
        failure.case_id for failure in failures if failure.severity == "critical"
    ]
    safe_to_merge = not failures
    summary = {
        "branch": report.branch if report else _git_branch(),
        "commit": report.commit if report else _git_commit(),
        "sample_only": sample_only,
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "failed_cases": len(failures),
    }
    codex_fix_prompt = generate_codex_fix_prompt(
        [failure.model_dump(mode="json") for failure in failures],
        summary=summary,
    )
    return HealthcareEvalFailureReport(
        timestamp=report.timestamp if report else datetime.now(UTC).isoformat(),
        branch=summary["branch"],
        commit=summary["commit"],
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=len(failures),
        skipped_or_xfailed_cases=skipped,
        overall_status="SAFE_TO_MERGE" if safe_to_merge else "BLOCK_MERGE",
        safe_to_merge=safe_to_merge,
        sample_only=sample_only,
        sample_notice=(
            "SAMPLE ONLY \u2014 NOT A REAL EVAL FAILURE" if sample_only else None
        ),
        critical_failures=critical_failures,
        failures=failures,
        codex_fix_prompt=codex_fix_prompt,
    )


def write_eval_failure_report(
    report_or_cases: EvalReport
    | dict[str, Any]
    | list[EvalCaseReport | dict[str, Any]],
    output_dir: str | Path = "eval_reports",
    *,
    sample_only: bool = False,
) -> HealthcareEvalFailureReport:
    """Write Markdown, JSON, and Codex prompt files for failed healthcare evals."""

    failure_report = build_failure_report(report_or_cases, sample_only=sample_only)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    json_path = output_path / "latest-healthcare-eval-failure-report.json"
    markdown_path = output_path / "latest-healthcare-eval-failure-report.md"
    prompt_path = output_path / "latest-codex-fix-prompt.md"

    json_path.write_text(
        json.dumps(failure_report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(
        _render_failure_markdown(failure_report),
        encoding="utf-8",
    )
    prompt_path.write_text(failure_report.codex_fix_prompt, encoding="utf-8")
    return failure_report


def _render_markdown(report: EvalReport) -> str:
    status = "SAFE TO MERGE" if report.safe_to_merge else "NOT SAFE TO MERGE"
    lines = [
        "# Healthcare Eval Summary",
        "",
        f"- Timestamp: {report.timestamp}",
        f"- Branch: {report.branch or 'unknown'}",
        f"- Commit: {report.commit or 'unknown'}",
        f"- Status: {status}",
        f"- Total cases: {report.total_cases}",
        f"- Passed: {report.passed}",
        f"- Failed: {report.failed}",
        f"- Xfailed/skipped: {report.xfailed_or_skipped}",
        f"- Recommended next action: {report.recommended_next_action}",
        "",
        "## Cases",
        "",
        "| Suite | Case | Result | Score | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in report.cases:
        result = "PASS" if case.passed else "FAIL"
        lines.append(
            f"| {case.suite} | {case.case_id} | {result} | "
            f"{case.score:.2f} | {case.reason} |"
        )
    return "\n".join(lines) + "\n"


def _render_failure_markdown(report: HealthcareEvalFailureReport) -> str:
    lines = [
        "# Healthcare Eval Failure Report",
        "",
    ]
    if report.sample_notice:
        lines.extend([report.sample_notice, ""])

    lines.extend(
        [
            "## Summary",
            "",
            f"- Timestamp: {report.timestamp}",
            f"- Git branch: {report.branch or 'unknown'}",
            f"- Git commit: {report.commit or 'unknown'}",
            f"- Total cases: {report.total_cases}",
            f"- Passed cases: {report.passed_cases}",
            f"- Failed cases: {report.failed_cases}",
            f"- Skipped/xfail cases: {report.skipped_or_xfailed_cases}",
            f"- Overall status: {report.overall_status}",
            "",
            "## Critical Failures",
            "",
        ]
    )

    if not report.failures:
        lines.extend(["No failed healthcare eval cases were supplied.", ""])
    for failure in report.failures:
        lines.extend(
            [
                f"### {failure.case_id}",
                "",
                f"- Case ID: {failure.case_id}",
                f"- Suite name: {failure.suite}",
                f"- Severity: {failure.severity}",
                f"- Failed scorer: {failure.failed_scorer}",
                f"- Expected behavior: {failure.expected_behavior}",
                f"- Observed behavior: {failure.observed_behavior}",
                f"- Reason: {failure.reason}",
                "- Relevant details:",
                "",
                "```json",
                _json_details(failure.details),
                "```",
                (
                    "- Clinical safety may be affected: yes"
                    if failure.clinical_safety_may_be_affected
                    else "- Clinical safety may be affected: no"
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Likely Cause",
            "",
        ]
    )
    if report.failures:
        for category in _failure_categories(report.failures):
            matching = [
                failure for failure in report.failures if failure.category == category
            ]
            files = _dedupe(
                file for failure in matching for file in failure.likely_files
            )
            lines.extend([f"### {category}", ""])
            lines.extend(f"- {file}" for file in files)
            lines.append("")
    else:
        lines.extend(["No likely-cause mapping is available without failures.", ""])

    lines.extend(["## Safety Constraints", ""])
    lines.extend(f"- {constraint}" for constraint in SAFETY_CONSTRAINTS)
    lines.extend(["", "## Recommended Fix Strategy", ""])
    if report.failures:
        for category in _failure_categories(report.failures):
            strategies = _dedupe(
                failure.suggested_fix_strategy
                for failure in report.failures
                if failure.category == category
            )
            lines.extend([f"### {category}", ""])
            lines.extend(f"- {strategy}" for strategy in strategies)
            lines.append("")
    else:
        lines.extend(["No fix is recommended because no failures were supplied.", ""])

    lines.extend(["## Rerun Commands", "", "```bash"])
    lines.extend(RERUN_COMMANDS)
    lines.extend(["```", "", "## Generated Codex Prompt", "", "```markdown"])
    lines.extend(report.codex_fix_prompt.rstrip().splitlines())
    lines.extend(["```", ""])
    return "\n".join(lines)


def _failure_item_from_case(case: EvalCaseReport) -> EvalFailureItem:
    details = dict(case.details)
    scorer_name = (
        case.scorer_name
        or _detail_string(details, "scorer_name")
        or _detail_string(details, "failed_scorer")
        or case.suite
    )
    failure_data = case.model_dump(mode="json")
    failure_data["failed_scorer"] = scorer_name
    classification = classify_failure(failure_data)
    expected_behavior = (
        case.expected_behavior
        or _detail_string(details, "expected_behavior")
        or classification.expected_behavior
    )
    observed_behavior = (
        case.observed_behavior
        or _detail_string(details, "observed_behavior")
        or _observed_from_details(case, details)
    )
    return EvalFailureItem(
        case_id=case.case_id,
        suite=case.suite,
        category=classification.category,
        severity=classification.severity,
        failed_scorer=scorer_name or classification.failed_scorer,
        expected_behavior=expected_behavior,
        observed_behavior=observed_behavior,
        reason=case.reason,
        details=details,
        likely_files=classification.likely_files,
        suggested_fix_strategy=classification.suggested_fix_strategy,
        safety_constraints=classification.safety_constraints,
        rerun_commands=classification.rerun_commands,
        clinical_safety_may_be_affected=(
            classification.clinical_safety_may_be_affected
        ),
    )


def _coerce_eval_report(value: Any) -> EvalReport | None:
    if isinstance(value, EvalReport):
        return value
    if isinstance(value, dict) and "cases" in value:
        return EvalReport.model_validate(value)
    return None


def _coerce_cases(value: Any) -> list[EvalCaseReport]:
    if not isinstance(value, list):
        raise TypeError("Expected EvalReport, eval report dict, or list of cases.")
    return [
        case
        if isinstance(case, EvalCaseReport)
        else EvalCaseReport.model_validate(case)
        for case in value
    ]


def _detail_string(details: dict[str, Any], key: str) -> str | None:
    value = details.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _observed_from_details(case: EvalCaseReport, details: dict[str, Any]) -> str:
    interesting_keys = (
        "finalized_at",
        "min_turns",
        "finalization_reason",
        "final_disposition",
        "missing_red_flags",
        "missing_sbar_fields",
        "matched_patterns",
        "failed_closed",
        "healthcare_intake_completeness",
        "healthcare_finalization_blocked_reason",
    )
    observed = {
        key: details[key]
        for key in interesting_keys
        if key in details and details[key] not in (None, "", [], {})
    }
    if observed:
        return _json_details(observed)
    return case.reason


def _json_details(details: dict[str, Any]) -> str:
    try:
        return json.dumps(details, indent=2, sort_keys=True)
    except TypeError:
        return json.dumps(_json_safe(details), indent=2, sort_keys=True)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _failure_categories(failures: list[EvalFailureItem]) -> list[FailureCategory]:
    return _dedupe(failure.category for failure in failures)


def _dedupe(values: Any) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _git_branch() -> str | None:
    env_branch = os.getenv("GITHUB_HEAD_REF") or os.getenv("GITHUB_REF_NAME")
    if env_branch:
        return env_branch

    git_dir = _find_git_dir()
    head = _read_git_file(git_dir / "HEAD") if git_dir else None
    if head and head.startswith("ref: refs/heads/"):
        return head.removeprefix("ref: refs/heads/")
    return None


def _git_commit() -> str | None:
    env_sha = os.getenv("GITHUB_SHA")
    if env_sha:
        return env_sha[:7]

    git_dir = _find_git_dir()
    if not git_dir:
        return None

    head = _read_git_file(git_dir / "HEAD")
    if not head:
        return None

    if not head.startswith("ref: "):
        return head[:7]

    ref_name = head.removeprefix("ref: ").strip()
    ref_sha = _read_git_file(git_dir / ref_name)
    if ref_sha:
        return ref_sha[:7]
    return _packed_ref_sha(git_dir / "packed-refs", ref_name)


def _find_git_dir(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        marker = directory / ".git"
        if marker.is_dir():
            return marker
        if marker.is_file():
            marker_text = _read_git_file(marker)
            if marker_text and marker_text.startswith("gitdir:"):
                git_dir = Path(marker_text.removeprefix("gitdir:").strip())
                if not git_dir.is_absolute():
                    git_dir = directory / git_dir
                return git_dir.resolve()
    return None


def _read_git_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _packed_ref_sha(packed_refs_path: Path, ref_name: str) -> str | None:
    packed_refs = _read_git_file(packed_refs_path)
    if not packed_refs:
        return None
    for line in packed_refs.splitlines():
        if line.startswith("#") or line.startswith("^"):
            continue
        sha, _, ref = line.partition(" ")
        if ref == ref_name:
            return sha[:7]
    return None
