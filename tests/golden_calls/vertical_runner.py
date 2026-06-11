"""Vertical golden-call runner (PR 1) — Birchwood collision + insurance FNOL.

Runs scripted-intake field sets through each vertical's PRODUCTION parsing
and deterministic classification (no LLM, no Twilio). Healthcare golden
calls keep their own runner (runner.py) and are untouched.

Case JSON schema (vertical_cases/<vertical>/*.json):
  case_id, description, vertical ("automotive_collision" | "insurance"),
  scripted_fields (dict of raw field values as captured by voice intake),
  dynamic_text (optional final-turn text), severity,
  expected_outcome (str or list of acceptable),
  expected_flags_contain / expected_flags_absent (optional lists),
  expected_rules_contain (optional list),
  expected_human_review (optional bool),
  expected_missing_contains (optional list),
  expected_injuries_mentioned (optional bool, insurance only)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VERTICAL_CASES_DIR = Path(__file__).parent / "vertical_cases"

REQUIRED_CASE_KEYS = [
    "case_id",
    "description",
    "vertical",
    "scripted_fields",
    "severity",
    "expected_outcome",
]


@dataclass
class VerticalGoldenResult:
    case_id: str
    vertical: str
    outcome: str
    flags: list[str] = field(default_factory=list)
    rules_triggered: list[str] = field(default_factory=list)
    human_review_required: bool = False
    missing_information: list[str] = field(default_factory=list)
    injuries_mentioned: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def load_vertical_cases() -> list[dict]:
    cases: list[dict] = []
    for json_file in sorted(VERTICAL_CASES_DIR.rglob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            case = json.load(f)
        missing = [k for k in REQUIRED_CASE_KEYS if k not in case]
        if missing:
            raise RuntimeError(f"{json_file.name} missing keys: {missing}")
        if case["vertical"] not in ("automotive_collision", "insurance"):
            raise RuntimeError(f"{json_file.name}: unknown vertical")
        cases.append(case)
    if not cases:
        raise RuntimeError(f"No vertical golden cases in {VERTICAL_CASES_DIR}")
    return cases


def _session_with_fields(fields: dict[str, Any]):
    from src.orchestrator.schemas import OrchestratorSession

    session = OrchestratorSession(session_id="golden", call_sid="CA-GOLDEN")
    session.channel_metadata["scripted_intake"] = {
        "fields": dict(fields),
        "completed": True,
    }
    return session


def run_vertical_case(case: dict) -> VerticalGoldenResult:
    try:
        if case["vertical"] == "automotive_collision":
            return _run_birchwood(case)
        return _run_insurance(case)
    except Exception as exc:  # fail closed: an error is a failing result
        return VerticalGoldenResult(
            case_id=case["case_id"],
            vertical=case["vertical"],
            outcome="ERROR",
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_birchwood(case: dict) -> VerticalGoldenResult:
    from src.verticals.automotive_collision.rules import classify_collision_intake
    from src.verticals.automotive_collision.workflow import _intake_from_session

    session = _session_with_fields(case["scripted_fields"])
    intake = _intake_from_session(session)
    assessment = classify_collision_intake(
        intake, dynamic_text=case.get("dynamic_text", "")
    )
    return VerticalGoldenResult(
        case_id=case["case_id"],
        vertical=case["vertical"],
        outcome=assessment.outcome,
        flags=list(assessment.flags),
        rules_triggered=list(assessment.rules_triggered),
        human_review_required=assessment.human_review_required,
        missing_information=list(assessment.missing_information),
        injuries_mentioned="injuries_reported" in assessment.flags,
        raw=assessment.model_dump(mode="json"),
    )


def _run_insurance(case: dict) -> VerticalGoldenResult:
    from src.verticals.insurance.rules import classify_insurance_claim
    from src.verticals.insurance.workflow import _intake_from_session

    session = _session_with_fields(case["scripted_fields"])
    intake = _intake_from_session(session)
    assessment = classify_insurance_claim(
        intake, dynamic_text=case.get("dynamic_text", "")
    )
    return VerticalGoldenResult(
        case_id=case["case_id"],
        vertical=case["vertical"],
        outcome=assessment.disposition,
        flags=list(assessment.safety_flags),
        rules_triggered=list(assessment.rules_triggered),
        human_review_required=assessment.human_review_required,
        missing_information=list(assessment.missing_information),
        injuries_mentioned=bool(assessment.injuries_mentioned),
        raw=assessment.model_dump(mode="json"),
    )
