"""Birchwood collision safety hardening (additive).

Covers the deterministic hazard/scene/legal escalation, the restricted-advice
boundaries (output post-check + caller-request detection), the rules overlays
(ESCALATE_SAFETY / HUMAN_REVIEW, injury behavior preserved, no false positives),
fail-closed behavior, and one offline-simulation assertion per safety scenario.
"""

from __future__ import annotations

import pytest

from src.verticals.automotive_collision.advice_boundaries import (
    RESTRICTED_ADVICE_SAFE_REPLY,
    detect_advice_request,
    enforce_advice_boundaries,
    scan_restricted_advice,
)
from src.verticals.automotive_collision.constants import (
    BIRCHWOOD_COLLISION_DISCLAIMERS,
)
from src.verticals.automotive_collision.rules import classify_collision_intake
from src.verticals.automotive_collision.safety_escalation import (
    scan_collision_safety,
)
from src.verticals.automotive_collision.schemas import AutomotiveCollisionIntake


def _classify(dynamic_text: str = "", **fields):
    return classify_collision_intake(
        AutomotiveCollisionIntake(**fields), dynamic_text=dynamic_text
    )


# ---------------------------------------------------------------------------
# Safety-escalation scanner: every trigger category fires
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,category",
    [
        ("there is smoke coming from the engine", "hazard"),
        ("the car is on fire", "hazard"),
        ("gas is leaking from the tank", "hazard"),
        ("a fuel leak under the car", "hazard"),
        ("the airbags deployed in the crash", "hazard"),
        ("we're stuck in the middle of the highway blocking traffic", "hazard"),
        ("it just happened, I'm still at the scene", "scene"),
        ("I'm trapped in the car and can't get out", "scene"),
        ("I'm really scared and shaking, please help", "scene"),
        ("the other driver is disputing whose fault it was", "legal"),
        ("they said they will sue me and get a lawyer", "legal"),
        ("someone died in the other vehicle", "legal"),
        ("it was a hit and run", "legal"),
    ],
)
def test_safety_scanner_triggers(text, category):
    scan = scan_collision_safety(text)
    assert scan.triggered
    assert category in scan.categories


@pytest.mark.parametrize(
    "text",
    [
        "I got rear-ended and the rear bumper is smashed but it still drives",
        "the whole front end is crushed in, it is not safe to drive",
        "a rock cracked my windshield on the highway, glass only",
        "another driver backed into me in a parking lot, going through insurance",
        "I was sideswiped on the freeway, passenger side panels damaged",
        "the police came and took a report",
    ],
)
def test_safety_scanner_no_false_positives_on_routine_collisions(text):
    assert not scan_collision_safety(text).triggered


# ---------------------------------------------------------------------------
# Restricted-advice boundaries: output post-check + caller-request detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,category",
    [
        ("Yes, you are covered for this.", "coverage"),
        ("Don't worry, we'll cover the repair.", "coverage"),
        ("Your claim is approved.", "coverage"),
        ("It's the other driver's fault.", "fault"),
        ("You are at fault for this collision.", "fault"),
        ("The repair will cost about $2,400.", "cost"),
        ("The estimate is around 3000 dollars.", "cost"),
        ("You should get a lawyer and sue them.", "legal"),
        ("You probably have a concussion.", "medical"),
        ("It's just whiplash, you're fine.", "medical"),
    ],
)
def test_scan_restricted_advice_flags_violations(text, category):
    assert category in scan_restricted_advice(text)


@pytest.mark.parametrize(
    "text",
    BIRCHWOOD_COLLISION_DISCLAIMERS
    + [
        # The deterministic caller-facing templates must pass clean — a false
        # positive here would sanitize legitimate live-pilot text.
        "This doesn't confirm coverage, pricing, or an appointment yet - "
        "your advisor will confirm next steps.",
        "ORCA does not provide insurance advice or coverage decisions.",
        "The repair will be coordinated with your insurer.",
        "No problem at all - I've noted the repair as out of pocket.",
        "going through MPI with claim number CLM-DEMO-1001",
        "Most importantly - if anyone is injured or feels unwell, please get "
        "medical attention or call 9 1 1 first.",
    ],
)
def test_scan_restricted_advice_passes_legitimate_text(text):
    assert scan_restricted_advice(text) == []


def test_enforce_advice_boundaries_replaces_and_escalates():
    result = enforce_advice_boundaries("You are at fault and it will cost $2000.")
    assert result.escalate is True
    assert set(result.violations) >= {"fault", "cost"}
    assert result.safe_text == RESTRICTED_ADVICE_SAFE_REPLY


def test_enforce_advice_boundaries_passes_clean_text_unchanged():
    clean = "Thanks - a Birchwood advisor will call you back shortly."
    result = enforce_advice_boundaries(clean)
    assert result.escalate is False
    assert result.violations == []
    assert result.safe_text == clean


@pytest.mark.parametrize(
    "text,category",
    [
        ("will my insurance cover this?", "coverage"),
        ("is this covered?", "coverage"),
        ("how much will it cost to fix?", "cost"),
        ("what's the estimate?", "cost"),
        ("whose fault is it?", "fault"),
        ("should I get a lawyer?", "legal"),
        ("do I need to see a doctor?", "medical"),
    ],
)
def test_detect_advice_request_flags_questions(text, category):
    assert category in detect_advice_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "I'm going through insurance, the claim number is CLM-DEMO-1001",
        "I'll be paying out of pocket myself",
        "the front bumper has body damage",
    ],
)
def test_detect_advice_request_ignores_routine_statements(text):
    assert detect_advice_request(text) == []


# ---------------------------------------------------------------------------
# Rules overlays
# ---------------------------------------------------------------------------


def _complete_fields(**overrides):
    base = dict(
        caller_name="Demo Caller",
        phone="2045550100",
        vehicle_year=2020,
        vehicle_make="Toyota",
        vehicle_model="Camry",
        is_drivable=True,
        damage_type="front bumper body damage",
        incident_description="parking lot bump",
        incident_datetime="yesterday",
        incident_location="a parking lot",
        injuries_state="denied",
        filing_insurance_claim=True,
        claim_number="CLM-DEMO-9001",
        is_rebuilt_or_salvage=False,
        confirmation_ack="yes",
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "trigger",
    [
        "there is smoke and the engine caught fire",
        "I'm trapped and it just happened",
        "they are disputing fault and getting a lawyer",
    ],
)
def test_classify_routes_safety_triggers_to_escalate_safety(trigger):
    assessment = _classify(dynamic_text=trigger, **_complete_fields())
    assert assessment.outcome == "ESCALATE_SAFETY"
    assert assessment.recommended_routing == "ESCALATE_SAFETY"
    assert assessment.human_review_required is True
    assert "safety_escalation" in assessment.flags


@pytest.mark.parametrize(
    "question,category",
    [
        ("will my insurance cover this", "coverage"),
        ("how much will it cost to fix", "cost"),
    ],
)
def test_classify_routes_advice_requests_to_human_review(question, category):
    assessment = _classify(dynamic_text=question, **_complete_fields())
    assert assessment.outcome == "HUMAN_REVIEW"
    assert assessment.human_review_required is True
    assert f"restricted_advice_requested:{category}" in assessment.flags


def test_safety_escalation_takes_precedence_over_advice_request():
    # Both a hazard and a coverage question present -> ESCALATE_SAFETY wins.
    assessment = _classify(
        dynamic_text="the car is on fire - also will insurance cover this?",
        **_complete_fields(),
    )
    assert assessment.outcome == "ESCALATE_SAFETY"


def test_injury_behavior_unchanged_not_escalate_safety():
    # Injury (no hazard/scene/legal) keeps the base outcome + injury flag —
    # this overlay is intentionally additive and does not change injury routing.
    assessment = _classify(
        dynamic_text="my neck has been sore since it happened",
        **_complete_fields(injuries_state=None),
    )
    assert assessment.outcome != "ESCALATE_SAFETY"
    assert "injuries_reported" in assessment.flags
    assert assessment.human_review_required is True


def test_benign_complete_intake_not_escalated():
    assessment = _classify(**_complete_fields())
    assert assessment.outcome == "COMPLETED_INTAKE"
    assert "safety_escalation" not in assessment.flags
    assert not any(
        f.startswith("restricted_advice_requested") for f in assessment.flags
    )


def test_safety_scan_failure_fails_closed(monkeypatch):
    # A scan exception must escalate rather than proceed (fail-closed).
    monkeypatch.setattr(
        "src.verticals.automotive_collision.rules.scan_collision_safety",
        lambda text: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assessment = _classify(**_complete_fields())
    assert assessment.outcome == "ESCALATE_SAFETY"
    assert assessment.human_review_required is True


# ---------------------------------------------------------------------------
# One offline-simulation assertion per safety scenario (end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario,expected_outcome,expected_flag",
    [
        ("hazard_fire_escalate", "ESCALATE_SAFETY", "safety:hazard"),
        ("active_scene_escalate", "ESCALATE_SAFETY", "safety:scene"),
        ("disputed_liability_escalate", "ESCALATE_SAFETY", "safety:legal"),
        (
            "coverage_question_human_review",
            "HUMAN_REVIEW",
            "restricted_advice_requested:coverage",
        ),
        (
            "cost_question_human_review",
            "HUMAN_REVIEW",
            "restricted_advice_requested:cost",
        ),
    ],
)
def test_offline_simulation_safety_scenarios(scenario, expected_outcome, expected_flag):
    from scripts.simulate_birchwood_call import simulate

    result = simulate(scenario)
    assert result["outcome"] == expected_outcome
    assert expected_flag in result["flags"]
