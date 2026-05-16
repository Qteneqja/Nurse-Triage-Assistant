import json
import subprocess
import sys
from pathlib import Path

from src.evals.eval_report_writer import EvalCaseReport, write_eval_failure_report


def test_markdown_report_includes_block_merge_when_critical_failures_exist(tmp_path):
    output_dir = tmp_path / "reports"
    write_eval_failure_report(
        [
            EvalCaseReport(
                case_id="abdominal_pain_premature_finalization_001",
                suite="premature_finalization",
                passed=False,
                score=0.0,
                scorer_name="deterministic_no_premature_finalization",
                reason=(
                    "Routine finalization occurred before the minimum dynamic turn gate."
                ),
                details={"finalized_at": 2, "min_turns": 4},
            )
        ],
        output_dir=output_dir,
    )

    markdown = (output_dir / "latest-healthcare-eval-failure-report.md").read_text(
        encoding="utf-8"
    )

    assert "# Healthcare Eval Failure Report" in markdown
    assert "Overall status: BLOCK_MERGE" in markdown
    assert "abdominal_pain_premature_finalization_001" in markdown


def test_json_report_is_valid_parseable_json(tmp_path):
    output_dir = tmp_path / "reports"
    write_eval_failure_report(
        [
            EvalCaseReport(
                case_id="sbar_missing_fields_synthetic_001",
                suite="sbar",
                passed=False,
                score=0.0,
                scorer_name="deterministic_sbar_completeness",
                reason="Finalized healthcare session is missing SBAR fields.",
                details={"missing_sbar_fields": ["background"]},
            )
        ],
        output_dir=output_dir,
    )

    payload = json.loads(
        (output_dir / "latest-healthcare-eval-failure-report.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["overall_status"] == "BLOCK_MERGE"
    assert payload["failures"][0]["category"] == "sbar_completeness"
    assert payload["failures"][0]["severity"] == "high"


def test_sample_failure_cli_generates_files_in_temp_output_directory(tmp_path):
    output_dir = tmp_path / "sample-report"
    script = Path("scripts/generate_eval_failure_report.py")

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--sample-failure",
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    markdown_path = output_dir / "latest-healthcare-eval-failure-report.md"
    json_path = output_dir / "latest-healthcare-eval-failure-report.json"
    prompt_path = output_dir / "latest-codex-fix-prompt.md"
    assert markdown_path.exists()
    assert json_path.exists()
    assert prompt_path.exists()
    assert "SAMPLE ONLY" in markdown_path.read_text(encoding="utf-8")
    assert "SAMPLE ONLY" in prompt_path.read_text(encoding="utf-8")


def test_reports_do_not_require_live_llm_api_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)

    report = write_eval_failure_report(
        [
            EvalCaseReport(
                case_id="fallback_malformed_llm_output_001",
                suite="fallback",
                passed=False,
                score=0.0,
                scorer_name="deterministic_invalid_output_fails_closed",
                reason="Invalid structured output did not fail closed safely.",
                details={"final_disposition": "SELF_CARE"},
            )
        ],
        output_dir=tmp_path,
    )

    assert report.overall_status == "BLOCK_MERGE"
