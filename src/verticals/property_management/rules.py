"""Deterministic maintenance urgency rules.

Rules intentionally run without an LLM for the Phase 11 MVP. The order is
severity-first: emergency rules win over same-day and scheduled repair rules.
"""

from __future__ import annotations

import re

from src.verticals.property_management.schemas import (
    MaintenanceClassification,
    MaintenanceIntake,
)


def classify_maintenance_request(
    intake: MaintenanceIntake,
) -> MaintenanceClassification:
    """Classify a maintenance request with deterministic rules."""

    text = _combined_text(intake)
    if not _has_minimum_issue_detail(intake):
        return MaintenanceClassification(
            disposition="HUMAN_REVIEW",
            urgency_reason="Insufficient maintenance issue detail for safe routing.",
            recommended_action="Have a property manager review and call the tenant.",
            vendor_type="property_manager",
            safety_flags=["insufficient_information"],
            rules_triggered=["property:insufficient_information"],
            confidence_score=0.45,
        )

    emergency = _emergency_match(text)
    if emergency:
        flag, reason, vendor = emergency
        return MaintenanceClassification(
            disposition="EMERGENCY",
            urgency_reason=reason,
            recommended_action=(
                "Dispatch emergency maintenance immediately. If there is immediate "
                "danger, instruct the tenant to call emergency services."
            ),
            vendor_type=vendor,
            safety_flags=[flag],
            rules_triggered=[f"property:emergency:{flag}"],
            confidence_score=0.95,
        )

    same_day = _same_day_match(text)
    if same_day:
        flag, reason, vendor = same_day
        return MaintenanceClassification(
            disposition="SAME_DAY",
            urgency_reason=reason,
            recommended_action="Create a same-day maintenance ticket and notify staff.",
            vendor_type=vendor,
            safety_flags=[flag],
            rules_triggered=[f"property:same_day:{flag}"],
            confidence_score=0.86,
        )

    scheduled = _scheduled_match(text)
    if scheduled:
        flag, reason, vendor = scheduled
        return MaintenanceClassification(
            disposition="SCHEDULED_REPAIR",
            urgency_reason=reason,
            recommended_action="Create a standard maintenance work order.",
            vendor_type=vendor,
            safety_flags=[],
            rules_triggered=[f"property:scheduled:{flag}"],
            confidence_score=0.78,
        )

    if _information_only_match(text):
        return MaintenanceClassification(
            disposition="INFORMATION_ONLY",
            urgency_reason="Caller appears to be requesting information only.",
            recommended_action="Route to the property office for informational follow-up.",
            vendor_type="property_manager",
            safety_flags=[],
            rules_triggered=["property:information_only"],
            confidence_score=0.7,
        )

    return MaintenanceClassification(
        disposition="HUMAN_REVIEW",
        urgency_reason="Request does not match deterministic maintenance rules.",
        recommended_action="Have a property manager review the request.",
        vendor_type="property_manager",
        safety_flags=["unclassified_issue"],
        rules_triggered=["property:unclassified"],
        confidence_score=0.5,
    )


def infer_vendor_type(issue_type: str | None, issue_description: str | None) -> str:
    """Infer the likely vendor category from issue text."""

    text = _normalize_text(" ".join([issue_type or "", issue_description or ""]))
    if _contains_any(text, ["gas", "carbon monoxide", "co alarm"]):
        return "emergency_services"
    if _contains_any(text, ["flood", "leak", "plumbing", "toilet", "sewage", "water"]):
        return "plumbing"
    if _contains_any(text, ["heat", "hot water", "furnace", "boiler", "hvac", "ac"]):
        return "hvac"
    if _contains_any(text, ["spark", "wire", "electrical", "outlet", "breaker"]):
        return "electrician"
    if _contains_any(text, ["lock", "key", "door", "break-in", "security"]):
        return "locksmith"
    if _contains_any(text, ["fridge", "refrigerator", "stove", "oven", "appliance"]):
        return "appliance_repair"
    return "general_maintenance"


def _combined_text(intake: MaintenanceIntake) -> str:
    return _normalize_text(
        " ".join(
            [
                intake.issue_type or "",
                intake.issue_description or "",
            ]
        )
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _has_minimum_issue_detail(intake: MaintenanceIntake) -> bool:
    return bool((intake.issue_type or "").strip() or (intake.issue_description or "").strip())


def _emergency_match(text: str) -> tuple[str, str, str] | None:
    rules = [
        (
            "active_flooding",
            ["active flooding", "flooding", "major leak", "water pouring", "burst pipe"],
            "Active flooding or uncontrolled water intrusion.",
            "plumbing",
        ),
        (
            "no_heat_cold_weather",
            ["no heat in winter", "no heat and cold", "no heat freezing", "no heat below freezing"],
            "No heat reported in winter or cold conditions.",
            "hvac",
        ),
        (
            "gas_smell",
            ["gas smell", "smell gas", "smells like gas", "natural gas"],
            "Possible gas leak.",
            "emergency_services",
        ),
        (
            "fire_or_smoke",
            ["fire", "smoke", "burning smell"],
            "Fire, smoke, or burning smell reported.",
            "emergency_services",
        ),
        (
            "electrical_sparks",
            ["electrical sparks", "sparks", "live wire", "exposed wire"],
            "Electrical sparks or exposed live wiring reported.",
            "electrician",
        ),
        (
            "sewage_backup",
            ["sewage backup", "sewer backup", "raw sewage"],
            "Sewage backup reported.",
            "plumbing",
        ),
        (
            "security_threat",
            ["break-in", "break in", "intruder", "security threat"],
            "Break-in or active security threat reported.",
            "emergency_services",
        ),
        (
            "urgent_lockout",
            ["locked out", "lockout", "cannot access unit", "can't access unit"],
            "Urgent lockout or inability to access the unit.",
            "locksmith",
        ),
        (
            "carbon_monoxide",
            ["carbon monoxide", "co alarm", "carbon monoxide alarm"],
            "Carbon monoxide alarm or concern reported.",
            "emergency_services",
        ),
        (
            "structural_collapse",
            ["structural collapse", "ceiling collapse", "wall collapsed", "roof collapse"],
            "Possible structural collapse reported.",
            "emergency_services",
        ),
    ]
    for flag, phrases, reason, vendor in rules:
        if _contains_any(text, phrases):
            return flag, reason, vendor
    return None


def _same_day_match(text: str) -> tuple[str, str, str] | None:
    rules = [
        (
            "no_hot_water",
            ["no hot water", "hot water not working"],
            "No hot water reported.",
            "hvac",
        ),
        (
            "only_toilet_not_working",
            ["only bathroom", "only toilet", "toilet not working"],
            "Toilet issue may affect the only working bathroom.",
            "plumbing",
        ),
        (
            "fridge_not_working",
            ["fridge not working", "refrigerator not working", "fridge broken"],
            "Refrigerator not working.",
            "appliance_repair",
        ),
        (
            "minor_leak",
            ["minor leak", "small leak", "leaking under sink"],
            "Minor leak reported.",
            "plumbing",
        ),
        (
            "weak_heat",
            ["heat weak", "heat barely working", "heat not enough"],
            "Heat is weak but not absent.",
            "hvac",
        ),
        (
            "outlet_no_sparks",
            ["outlet not working", "electrical outlet not working"],
            "Electrical outlet issue reported without sparks.",
            "electrician",
        ),
        (
            "exterior_security",
            ["broken exterior door", "broken window", "window won't lock", "door won't lock"],
            "Exterior door or window security concern.",
            "general_maintenance",
        ),
    ]
    for flag, phrases, reason, vendor in rules:
        if _contains_any(text, phrases):
            return flag, reason, vendor
    return None


def _scheduled_match(text: str) -> tuple[str, str, str] | None:
    rules = [
        (
            "appliance_non_urgent",
            ["appliance", "dishwasher", "stove", "oven", "microwave"],
            "Non-urgent appliance issue.",
            "appliance_repair",
        ),
        (
            "dripping_faucet",
            ["dripping faucet", "faucet dripping", "slow drip"],
            "Dripping faucet or slow drip.",
            "plumbing",
        ),
        (
            "cosmetic_damage",
            ["cosmetic", "paint", "scratch", "cabinet", "floor damage"],
            "Cosmetic or non-urgent damage.",
            "general_maintenance",
        ),
        (
            "noise_complaint",
            ["noise complaint", "loud neighbor", "noise"],
            "Noise complaint.",
            "property_manager",
        ),
        (
            "parking_issue",
            ["parking", "garage remote", "parking spot"],
            "Parking-related issue.",
            "property_manager",
        ),
        (
            "general_maintenance",
            ["general maintenance", "maintenance request", "repair request"],
            "General maintenance request.",
            "general_maintenance",
        ),
    ]
    for flag, phrases, reason, vendor in rules:
        if _contains_any(text, phrases):
            return flag, reason, vendor
    return None


def _information_only_match(text: str) -> bool:
    return _contains_any(text, ["question", "information", "office hours", "how do i"])


def _contains_any(text: str, phrases: list[str]) -> bool:
    return any(phrase in text for phrase in phrases)
