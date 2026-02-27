"""
Simulation Runner

Feeds scripted caller turns through the orchestrator and outputs
trace summaries + final dispositions. No real phone calls or Twilio needed.

Usage:
    python -m scripts.simulate_calls                         # run all scenarios
    python -m scripts.simulate_calls --scenario chest_pain   # run one scenario
    python -m scripts.simulate_calls --mock                  # use mock LLM (no API calls)
    python -m scripts.simulate_calls --file custom.json      # use custom scenario file

Requires DEEPSEEK_API_KEY in environment (unless --mock is used).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Literal, Optional
from unittest.mock import AsyncMock, MagicMock

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.schemas import (
    AuditTrace,
    DispositionCategory,
    FinalizeOutput,
    IntakeTurnOutput,
    IntakeStatePatch,
    OrchestratorSession,
    StructuredIntakeState,
)
from src.llm.client import StructuredLLMClient, get_structured_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_SCENARIOS_PATH = Path(__file__).parent / "scenarios.json"


# -----------------------------------------------------------------------
# Mock LLM client for offline simulation
# -----------------------------------------------------------------------

class MockLLMClient:
    """Mock LLM that returns plausible responses without API calls.

    Used for testing the orchestration flow without needing an API key.
    """

    def __init__(self) -> None:
        self._turn_count = 0

    async def call(self, messages, output_schema, **kwargs):
        """Return mock responses matching the requested schema."""
        self._turn_count += 1

        if output_schema == IntakeTurnOutput:
            # Simulate progressive confidence
            confidence = min(0.2 + self._turn_count * 0.15, 0.85)
            missing = ["onset_time", "symptom_severity", "meds", "allergies"]
            remaining = missing[min(self._turn_count, len(missing)):]

            return IntakeTurnOutput(
                extracted_fields_update=IntakeStatePatch(notes=f"Turn {self._turn_count} processed"),
                missing_fields_prioritized=remaining,
                next_question="Can you tell me more about when this started?" if remaining else "Is there anything else?",
                llm_safety_flags=[],
                confidence=confidence,
            )

        elif output_schema == FinalizeOutput:
            return FinalizeOutput(
                disposition=DispositionCategory.HUMAN_REVIEW,
                disposition_reasoning="Mock disposition — human review recommended for simulated scenario",
                safety_net_instructions=["If symptoms worsen, please call 911 or go to the nearest emergency room."],
                sbar_report=(
                    "S: Patient completed simulated triage intake.\n"
                    "B: See conversation history.\n"
                    "A: Mock assessment — requires human review.\n"
                    "R: Recommend human clinician review."
                ),
                patient_summary=(
                    "Thank you for that information. A nurse will review your case "
                    "and contact you soon. If your symptoms worsen, please go to the "
                    "emergency room or call 911."
                ),
                llm_safety_flags=[],
            )

        raise ValueError(f"Unexpected schema: {output_schema}")


# -----------------------------------------------------------------------
# Simulation engine
# -----------------------------------------------------------------------

async def run_scenario(
    scenario: dict,
    orchestrator: Orchestrator,
    verbose: bool = True,
) -> dict:
    """Run a single scenario through the orchestrator.

    Args:
        scenario: Dict with keys: scenario_id, description, turns, demographics.
        orchestrator: Orchestrator instance.
        verbose: Print turn-by-turn details.

    Returns:
        Summary dict with results.
    """
    scenario_id = scenario["scenario_id"]
    description = scenario.get("description", "")
    turns = scenario["turns"]
    demographics = scenario.get("demographics", {})

    print(f"\n{'=' * 70}")
    print(f"SCENARIO: {scenario_id}")
    print(f"  {description}")
    print(f"  Demographics: {demographics}")
    print(f"  Turns: {len(turns)}")
    print(f"{'=' * 70}")

    # Create session
    session = OrchestratorSession(
        session_id=f"sim-{scenario_id}",
        max_turns=12,
        confidence_threshold=0.75,
    )

    # Seed demographics
    if demographics.get("name"):
        session.intake_state.caller_name = demographics["name"]
    if demographics.get("age"):
        try:
            session.intake_state.caller_age = int(demographics["age"])
        except ValueError:
            pass
    if demographics.get("sex"):
        _sex = str(demographics["sex"]).strip().lower()
        _SEX_MAP: dict[str, Literal["male", "female", "unknown"]] = {
            "m": "male", "male": "male",
            "f": "female", "female": "female",
            "u": "unknown", "unknown": "unknown",
        }
        session.intake_state.caller_sex = _SEX_MAP.get(_sex, "unknown")

    t0 = time.monotonic()
    final_action = None
    final_message = None
    turns_processed = 0

    for i, utterance in enumerate(turns, 1):
        if session.is_finalized:
            break

        if verbose:
            print(f"\n  Turn {i}: Caller says: \"{utterance[:80]}{'...' if len(utterance) > 80 else ''}\"")

        result = await orchestrator.process_turn(session, utterance)
        final_action = result["action"]
        final_message = result["message"]
        turns_processed = i

        if verbose:
            action_label = {
                "escalate": "ESCALATE",
                "finalize": "FINALIZE",
                "ask": "ASK",
            }.get(final_action, final_action.upper())
            print(f"  [{action_label}] \"{final_message[:80]}{'...' if len(final_message) > 80 else ''}\"")

            if result.get("intake_output"):
                io = result["intake_output"]
                print(f"    confidence={io.confidence:.2f}, fields_updated={list(io.extracted_fields_update.model_dump(exclude_none=True).keys())}")

    elapsed = (time.monotonic() - t0) * 1000

    # audit_trace is guaranteed non-None after model_post_init
    assert session.audit_trace is not None

    # Print summary
    print(f"\n{'-' * 70}")
    print(f"RESULT: {scenario_id}")
    print(f"  Turns processed: {turns_processed}/{len(turns)}")
    print(f"  Final action: {final_action}")
    print(f"  Total time: {elapsed:.0f}ms")

    if session.finalize_output:
        fo = session.finalize_output
        print(f"  Disposition: {fo.disposition.value}")
        print(f"  Reasoning: {fo.disposition_reasoning[:100]}")
        print(f"  Patient summary: {fo.patient_summary[:100]}")
    elif session.audit_trace.deterministic_rules_triggered:
        print(f"  Deterministic escalation: {session.audit_trace.deterministic_rules_triggered}")

    if session.safety_flags:
        print(f"  Safety flags: {[f.flag for f in session.safety_flags]}")

    print(f"\n  Intake State:")
    state = session.intake_state.model_dump(exclude_none=True, exclude_defaults=True)
    for k, v in state.items():
        print(f"    {k}: {v}")

    print(f"\n  Audit Trace ({len(session.audit_trace.entries)} entries):")
    for entry in session.audit_trace.entries:
        dur = f" ({entry.duration_ms:.0f}ms)" if entry.duration_ms else ""
        print(f"    [{entry.agent}] {entry.step}{dur}: {entry.output_summary or ''}")

    # Coercion summary
    if session.llm_coercions:
        print(f"\n  LLM coercions ({len(session.llm_coercions)}): {session.llm_coercions}")

    expected_min = scenario.get("expected_min_disposition")
    expected_max = scenario.get("expected_max_disposition")
    if expected_min:
        if session.finalize_output:
            actual = session.finalize_output.disposition.value
        elif session.audit_trace.deterministic_rules_triggered:
            actual = "ER_NOW"  # deterministic escalation
        else:
            actual = "N/A"
        under = _disposition_acceptable(actual, expected_min)
        over = not expected_max or not _disposition_over_triaged(actual, expected_max)
        if under and over:
            label = "PASS"
        elif not under:
            label = "UNDER-TRIAGE"
        else:
            label = "OVER-TRIAGE"
        bounds = f"min={expected_min}"
        if expected_max:
            bounds += f", max={expected_max}"
        print(f"\n  Expected: [{bounds}], Got: {actual} [{label}]")

    print(f"{'-' * 70}")

    return {
        "scenario_id": scenario_id,
        "turns_processed": turns_processed,
        "final_action": final_action,
        "disposition": session.finalize_output.disposition.value if session.finalize_output else (
            "ER_NOW" if session.audit_trace.deterministic_rules_triggered else None
        ),
        "deterministic_rules": session.audit_trace.deterministic_rules_triggered,
        "coercions": len(session.llm_coercions),
        "elapsed_ms": elapsed,
    }


# HUMAN_REVIEW is intentionally excluded — it's an uncertainty escalation,
# not a position on the clinical urgency ladder.
_URGENCY_ORDER = ["SELF_CARE", "ROUTINE", "SAME_DAY", "URGENT_CARE", "ER_NOW"]


def _disposition_acceptable(actual: str, expected_min: str) -> bool:
    """Check if actual disposition is at least as urgent as expected_min.

    HUMAN_REVIEW is always acceptable (conservative, never under-triage).
    """
    if actual == "HUMAN_REVIEW":
        return True
    if expected_min == "HUMAN_REVIEW":
        # Any concrete disposition satisfies a HUMAN_REVIEW minimum
        return True
    if actual not in _URGENCY_ORDER or expected_min not in _URGENCY_ORDER:
        return actual == expected_min
    return _URGENCY_ORDER.index(actual) >= _URGENCY_ORDER.index(expected_min)


def _disposition_over_triaged(actual: str, expected_max: str) -> bool:
    """Check if actual disposition is MORE urgent than expected_max.

    HUMAN_REVIEW is never considered over-triage.
    """
    if actual == "HUMAN_REVIEW":
        return False
    if expected_max == "HUMAN_REVIEW":
        # HUMAN_REVIEW max means "anything goes" — can't over-triage
        return False
    if actual not in _URGENCY_ORDER or expected_max not in _URGENCY_ORDER:
        return actual != expected_max
    return _URGENCY_ORDER.index(actual) > _URGENCY_ORDER.index(expected_max)


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Simulate triage calls through orchestrator")
    parser.add_argument("--file", type=str, default=str(DEFAULT_SCENARIOS_PATH),
                        help="Path to scenarios JSON file")
    parser.add_argument("--scenario", type=str, default=None,
                        help="Run only this scenario (by scenario_id)")
    parser.add_argument("--mock", action="store_true",
                        help="Use mock LLM (no API calls needed)")
    parser.add_argument("--quiet", action="store_true",
                        help="Only print final summary")
    args = parser.parse_args()

    # Load scenarios
    scenarios_path = Path(args.file)
    if not scenarios_path.exists():
        print(f"ERROR: Scenarios file not found: {scenarios_path}")
        sys.exit(1)

    with open(scenarios_path) as f:
        scenarios = json.load(f)

    if args.scenario:
        scenarios = [s for s in scenarios if args.scenario in s["scenario_id"]]
        if not scenarios:
            print(f"ERROR: No scenario matching '{args.scenario}'")
            sys.exit(1)

    # Create orchestrator
    if args.mock:
        print("Using MOCK LLM client (no API calls)")
        llm_client = MockLLMClient()
    else:
        print("Using REAL DeepSeek LLM client")
        llm_client = get_structured_client()

    orchestrator = Orchestrator(llm_client=llm_client)  # type: ignore

    # Run scenarios
    results = []
    for scenario in scenarios:
        try:
            result = await run_scenario(
                scenario,
                orchestrator,
                verbose=not args.quiet,
            )
            results.append(result)
        except Exception as e:
            print(f"\nERROR in scenario {scenario['scenario_id']}: {e}")
            results.append({
                "scenario_id": scenario["scenario_id"],
                "error": str(e),
            })

    # Final summary
    print(f"\n{'=' * 70}")
    print("SIMULATION SUMMARY")
    print(f"{'=' * 70}")
    for r in results:
        if "error" in r:
            print(f"  {r['scenario_id']}: ERROR - {r['error']}")
        else:
            disp = r.get("disposition") or "ESCALATED"
            rules = r.get("deterministic_rules", [])
            rules_str = f" [rules: {', '.join(rules)}]" if rules else ""
            coercion_count = r.get("coercions", 0)
            coercion_str = f" [coercions: {coercion_count}]" if coercion_count else ""
            print(f"  {r['scenario_id']}: {disp} ({r['turns_processed']} turns, {r['elapsed_ms']:.0f}ms){rules_str}{coercion_str}")


if __name__ == "__main__":
    asyncio.run(main())
