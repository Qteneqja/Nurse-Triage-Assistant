"""Minimal Birchwood collision-intake workflow (birchwood_collision_intake_min_v1).

Pure intake -> specialist handoff. Covers: schema, completeness rules,
no-estimate/no-advice behavior, the SHARED reactive emergency reflex (inherited
from the platform overlay), the vertical-scoping finding (no clinical red-flags),
registration without disturbing the live pilot, and one assertion per demo
scenario. Validated offline via the WorkflowEngine + the simulation runner.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.simulate_collision_min_call import SCENARIOS, simulate
from src.verticals.collision_intake_min.constants import (
    COLLISION_MIN_REQUIRED_FIELDS,
    COLLISION_MIN_WORKFLOW_ID,
)
from src.verticals.collision_intake_min.rules import (
    classify_collision_min_intake,
    detect_estimate_request,
)
from src.verticals.collision_intake_min.schemas import CollisionMinIntake


def _complete(**overrides) -> CollisionMinIntake:
    base = dict(
        caller_name="Demo Caller",
        callback_number="204 555 0100",
        vehicle_year=2020,
        vehicle_make="Toyota",
        vehicle_model="Camry",
        damage_description="front bumper",
        drivable_status="drivable",
        mpi_claim_opened=True,
        mpi_claim_number="CLM-DEMO-1",
    )
    base.update(overrides)
    return CollisionMinIntake(**base)


# ---------------------------------------------------------------------------
# Schema + completeness
# ---------------------------------------------------------------------------


def test_schema_round_trips_and_rejects_bad_disposition():
    from src.verticals.collision_intake_min.schemas import CollisionMinAssessment

    a = CollisionMinAssessment(
        disposition="READY_FOR_SPECIALIST", handoff_mode="warm_transfer"
    )
    assert a.disposition == "READY_FOR_SPECIALIST"
    with pytest.raises(Exception):
        CollisionMinAssessment(disposition="ER_NOW", handoff_mode="warm_transfer")


def test_complete_intake_ready_for_specialist():
    a = classify_collision_min_intake(_complete())
    assert a.disposition == "READY_FOR_SPECIALIST"
    assert a.handoff_mode == "warm_transfer"
    assert a.missing_information == []


def test_missing_required_field_routes_to_callback():
    a = classify_collision_min_intake(_complete(callback_number=None))
    assert a.disposition == "CALLBACK_NEEDED"
    assert "callback_number" in a.missing_information


def test_not_drivable_requires_location():
    a = classify_collision_min_intake(_complete(drivable_status="not_drivable"))
    assert a.disposition == "CALLBACK_NEEDED"
    assert "vehicle_location" in a.missing_information
    assert "needs_tow" in a.flags

    a2 = classify_collision_min_intake(
        _complete(drivable_status="not_drivable", vehicle_location="Main St")
    )
    assert a2.disposition == "READY_FOR_SPECIALIST"
    assert "needs_tow" in a2.flags


def test_mpi_claim_status_is_data_only():
    assert "mpi_claim_in_hand" in classify_collision_min_intake(_complete()).flags
    assert (
        "no_mpi_claim"
        in classify_collision_min_intake(
            _complete(mpi_claim_opened=False, mpi_claim_number=None)
        ).flags
    )


# ---------------------------------------------------------------------------
# No-estimate / no-advice behavior (plain hallucination-prevention)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "how much will it cost",
        "what's the estimate",
        "will MPI cover this",
        "is this covered",
        "whose fault is it",
        "how long will the repair take",
    ],
)
def test_detect_estimate_request_positives(text):
    assert detect_estimate_request(text) is True


@pytest.mark.parametrize(
    "text",
    ["my front bumper is dented", "it's a 2020 Toyota Camry", "I'm going through MPI"],
)
def test_detect_estimate_request_negatives(text):
    assert detect_estimate_request(text) is False


def test_estimate_question_is_deflected_not_answered():
    result = simulate("asks_for_estimate")
    assert result["outcome"] == "READY_FOR_SPECIALIST"
    assert "estimate_request_deflected" in result["flags"]
    spoken = result["assistant_text"].lower()
    assert "specialist will go over cost" in spoken
    # The agent must NOT state a number, coverage, or fault decision.
    assert "$" not in result["assistant_text"]
    assert "covered" not in spoken
    assert "your fault" not in spoken


# ---------------------------------------------------------------------------
# Reactive emergency reflex = SHARED platform baseline (not a collision feature)
# ---------------------------------------------------------------------------


def test_workflow_never_asks_about_injuries():
    from src.verticals.collision_intake_min.workflow import (
        BirchwoodCollisionMinIntakeWorkflow,
    )

    intake = BirchwoodCollisionMinIntakeWorkflow().get_scripted_intake_definition()
    field_names = [s.field_name for s in intake.stages]
    assert not any("injur" in f.lower() for f in field_names)
    assert not any("injur" in f.lower() for f in COLLISION_MIN_REQUIRED_FIELDS)


def test_spontaneous_injury_triggers_shared_reflex():
    result = simulate("emergency_reflex")
    # Inherited platform overlay: advisory spoken, record flagged, human review.
    assert "injuries_reported" in result["flags"]
    assert result["human_review_required"] is True
    assert "9 1 1" in result["assistant_text"]
    # Still a pure-intake outcome - NOT a clinical disposition.
    assert result["outcome"] == "READY_FOR_SPECIALIST"


# ---------------------------------------------------------------------------
# Vertical-scoping finding: collision-min inherits the shared baseline ONLY and
# none of the nurse-triage clinical red-flags / gate.
# ---------------------------------------------------------------------------


def test_vertical_scoping_no_clinical_redflags_in_package():
    pkg = Path("src/verticals/collision_intake_min")
    forbidden = (
        "score_red_flags",
        "run_red_flag_rules",
        "gate_triage_output",
        "check_all",
    )
    for path in pkg.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in src, f"{path} references clinical red-flag '{token}'"


def test_vertical_scoping_emergency_is_baseline_not_clinical():
    # A medical-emergency mention yields the SHARED injury baseline, never a
    # clinical triage disposition (ER_NOW/URGENT/etc.).
    result = simulate("emergency_reflex")
    assert result["outcome"] in {"READY_FOR_SPECIALIST", "CALLBACK_NEEDED"}
    assert result["outcome"] not in {"ER_NOW", "URGENT", "SCHEDULE", "SELF_CARE"}


# ---------------------------------------------------------------------------
# Registration (live pilot + healthcare default untouched)
# ---------------------------------------------------------------------------


def test_registers_without_disturbing_live_pilot_or_default():
    from src.platform.workflows.registry import (
        ensure_default_workflows_registered,
        reset_workflow_registry,
    )

    reset_workflow_registry()
    reg = ensure_default_workflows_registered()
    wf = reg.get(COLLISION_MIN_WORKFLOW_ID)
    assert wf is not None
    assert wf.get_definition().vertical == "automotive_collision_min"
    assert wf.get_definition().required_fields == list(COLLISION_MIN_REQUIRED_FIELDS)
    # Live pilot and healthcare default are untouched.
    assert reg.get("birchwood_collision_intake_v1") is not None
    assert reg.get_default_for_vertical("healthcare").get_definition().workflow_id == (
        "healthcare_triage_v1"
    )


# ---------------------------------------------------------------------------
# One assertion per demo scenario (offline, end-to-end via the engine)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_each_simulation_scenario(name):
    result = simulate(name)
    expected = SCENARIOS[name].get("expect_outcome")
    if expected:
        assert result["outcome"] == expected, name
    for flag in SCENARIOS[name].get("expect_flags", []):
        assert flag in result["flags"], (name, flag)


def test_demo_pack_structure_and_no_overpromise():
    demo = Path("demo/birchwood_collision_min")
    scenarios = json.loads((demo / "scenarios.json").read_text(encoding="utf-8"))
    assert len(scenarios) == 4
    forbidden = ["covered", "approved", "we will pay", "estimate is", "your fault"]
    for sc in scenarios:
        assert sc["workflow_id"] == COLLISION_MIN_WORKFLOW_ID
        expected_file = demo / "expected_outputs" / f"{sc['scenario_id']}.json"
        assert expected_file.exists(), sc["scenario_id"]
        payload = json.loads(expected_file.read_text(encoding="utf-8"))
        for field in [
            "workflow_id",
            "caller_name",
            "callback_number",
            "vehicle_year",
            "damage_description",
            "drivable_status",
            "recommended_routing",
            "handoff_mode",
            "flags",
            "disclaimers_given",
            "confidence",
            "human_review_required",
        ]:
            assert field in payload, (sc["scenario_id"], field)
    # No over-promise language anywhere in the demo pack.
    text = "\n".join(
        p.read_text(encoding="utf-8") for p in demo.rglob("*") if p.is_file()
    ).lower()
    for phrase in forbidden:
        assert phrase not in text, phrase
