"""Generate healthcare eval failure reports and Codex fix prompts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evals.eval_report_writer import (  # noqa: E402
    EvalCaseReport,
    write_eval_failure_report,
)


DEFAULT_INPUT = Path("eval_reports/latest-healthcare-eval-results.json")
DEFAULT_OUTPUT_DIR = Path("eval_reports")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.sample_failure:
        report_source = _sample_failure_cases()
        sample_only = True
    else:
        input_path = Path(args.input) if args.input else DEFAULT_INPUT
        if not input_path.exists():
            parser.error(
                f"input report not found: {input_path}. "
                "Provide --input or use --sample-failure."
            )
        report_source = _load_json(input_path)
        sample_only = False

    output_dir = Path(args.output_dir)
    report = write_eval_failure_report(
        report_source,
        output_dir=output_dir,
        sample_only=sample_only,
    )

    print(f"Wrote {output_dir / 'latest-healthcare-eval-failure-report.md'}")
    print(f"Wrote {output_dir / 'latest-healthcare-eval-failure-report.json'}")
    print(f"Wrote {output_dir / 'latest-codex-fix-prompt.md'}")
    print(f"Overall status: {report.overall_status}")
    if report.sample_notice:
        print(report.sample_notice)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic healthcare eval failure reports and "
            "ready-to-paste Codex fix prompts."
        )
    )
    parser.add_argument(
        "--input",
        help=(
            "Path to an eval JSON summary, for example "
            "eval_reports/latest-healthcare-eval-results.json."
        ),
    )
    parser.add_argument(
        "--sample-failure",
        action="store_true",
        help="Generate a clearly labeled sample failure report for previewing output.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for generated report files. Defaults to eval_reports.",
    )
    return parser


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sample_failure_cases() -> list[EvalCaseReport]:
    return [
        EvalCaseReport(
            case_id="sample_only_premature_finalization_preview",
            suite="sample_only_preview",
            passed=False,
            score=0.0,
            scorer_name="deterministic_no_premature_finalization",
            expected_behavior=(
                "SAMPLE ONLY: routine healthcare intake should stay open until "
                "minimum dynamic turns and completeness gates are satisfied."
            ),
            observed_behavior=(
                "SAMPLE ONLY: a mock routine session finalized at turn 2."
            ),
            reason=(
                "SAMPLE ONLY - NOT A REAL EVAL FAILURE. This mock failure previews "
                "the report and Codex prompt format."
            ),
            details={
                "sample_only": True,
                "sample_notice": "SAMPLE ONLY \u2014 NOT A REAL EVAL FAILURE",
                "failed_scorer": "deterministic_no_premature_finalization",
                "finalized_at": 2,
                "min_turns": 4,
                "finalization_reason": "sample_only_mock_finalization",
            },
        )
    ]


if __name__ == "__main__":
    raise SystemExit(main())
