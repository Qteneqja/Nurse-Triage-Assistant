"""Offline reader for the insurance FNOL demo pack.

This script intentionally does not call Twilio, LLMs, or external APIs. It reads
the demo scenario catalog plus static expected outputs and prints a concise
console summary suitable for sales demos or local validation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_ROOT = REPO_ROOT / "demo" / "insurance_fnol"
SCENARIOS_PATH = DEMO_ROOT / "scenarios.json"
EXPECTED_OUTPUTS_ROOT = DEMO_ROOT / "expected_outputs"
TRANSCRIPTS_ROOT = DEMO_ROOT / "transcripts"


def load_scenarios() -> list[dict[str, Any]]:
    """Load the static insurance demo scenarios."""

    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def run_scenario(
    scenario: dict[str, Any],
    *,
    include_transcript: bool = False,
) -> None:
    """Print one scenario summary and its expected output highlights."""

    expected = _load_expected_output(scenario)
    print(f"\n{'=' * 72}")
    print(f"{scenario['scenario_id']}: {scenario['title']}")
    print(f"{'=' * 72}")
    print(f"Claim type: {scenario['claim_type']}")
    print(f"Expected routing: {scenario['expected_routing']}")
    print(f"Dashboard summary: {scenario['dashboard_summary']}")

    print("\nExtracted key fields:")
    for field_name, value in _key_fields(expected).items():
        print(f"  - {field_name}: {value}")

    print("\nDisclaimer checklist:")
    for disclaimer in scenario.get("expected_disclaimers", []):
        present = disclaimer in expected.get("disclaimers_given", [])
        status = "present" if present else "missing"
        print(f"  - [{status}] {disclaimer}")

    print(f"\nDemo notes: {scenario['demo_notes']}")

    if include_transcript and scenario.get("transcript_file"):
        transcript = TRANSCRIPTS_ROOT / scenario["transcript_file"]
        if transcript.exists():
            print("\nTranscript:")
            print(transcript.read_text(encoding="utf-8"))


def print_available(scenarios: list[dict[str, Any]]) -> None:
    """Print available scenario IDs."""

    print("Available insurance FNOL demo scenarios:")
    for scenario in scenarios:
        print(
            f"  - {scenario['scenario_id']}: "
            f"{scenario['title']} ({scenario['expected_routing']})"
        )
    print("\nRun one scenario:")
    print("  python scripts/run_insurance_demo.py --scenario water_damage_mitigation")
    print("\nRun all scenarios:")
    print("  python scripts/run_insurance_demo.py --all")


def _load_expected_output(scenario: dict[str, Any]) -> dict[str, Any]:
    output_file = scenario.get("expected_output_file")
    if not output_file:
        raise ValueError(f"Scenario {scenario['scenario_id']} has no output file")
    path = EXPECTED_OUTPUTS_ROOT / output_file
    return json.loads(path.read_text(encoding="utf-8"))


def _key_fields(expected: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "workflow_id",
        "vertical",
        "claim_type",
        "caller_name",
        "callback_number",
        "policy_number",
        "loss_datetime",
        "loss_location",
        "emergency_or_safety_issue",
        "injuries_mentioned",
        "emergency_services_involved",
        "police_or_fire_report",
        "property_secure",
        "mitigation_needed",
        "documents_available",
        "missing_information",
        "recommended_routing",
        "confidence",
        "human_review_required",
    ]
    return {field: expected.get(field) for field in fields}


def _scenario_by_id(
    scenarios: list[dict[str, Any]],
    scenario_id: str,
) -> dict[str, Any] | None:
    for scenario in scenarios:
        if scenario["scenario_id"] == scenario_id:
            return scenario
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline insurance FNOL demo pack.",
    )
    parser.add_argument(
        "--scenario",
        help="Run one scenario by scenario_id.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run every scenario in scenarios.json.",
    )
    parser.add_argument(
        "--include-transcript",
        action="store_true",
        help="Print the transcript file when one exists.",
    )
    args = parser.parse_args()

    scenarios = load_scenarios()

    if args.all:
        for scenario in scenarios:
            run_scenario(
                scenario,
                include_transcript=args.include_transcript,
            )
        return 0

    if args.scenario:
        scenario = _scenario_by_id(scenarios, args.scenario)
        if scenario is None:
            print(f"Unknown scenario_id: {args.scenario}")
            print_available(scenarios)
            return 2
        run_scenario(
            scenario,
            include_transcript=args.include_transcript,
        )
        return 0

    print_available(scenarios)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
