"""Markdown and JSON report writer for healthcare eval runs."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.evals.healthcare_eval_scorers import EvalScoreResult


class EvalCaseReport(BaseModel):
    """Per-case eval result for report output."""

    case_id: str
    suite: str
    passed: bool
    score: float
    reason: str
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


def build_case_report(
    *,
    case_id: str,
    suite: str,
    score: EvalScoreResult,
    critical: bool = False,
) -> EvalCaseReport:
    """Create a report row from a deterministic scorer output."""

    details = dict(score.details)
    details["critical"] = critical
    return EvalCaseReport(
        case_id=case_id,
        suite=suite,
        passed=score.passed,
        score=score.score,
        reason=score.reason,
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
        branch=_git(["rev-parse", "--abbrev-ref", "HEAD"]),
        commit=_git(["rev-parse", "--short", "HEAD"]),
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


def _git(args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None
