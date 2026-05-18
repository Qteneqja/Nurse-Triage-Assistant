"""Deterministic rules for ORCA's Birchwood collision intake workflow."""

from __future__ import annotations

import re
from typing import Any

import src.config as config
from src.verticals.automotive_collision.constants import (
    BIRCHWOOD_COLLISION_DEFAULT_LOCATIONS,
    BIRCHWOOD_COLLISION_DEFAULT_LUXURY_BRANDS,
    BIRCHWOOD_COLLISION_DEFAULT_LUXURY_LOCATION,
    BIRCHWOOD_COLLISION_DISCLAIMERS,
    BIRCHWOOD_COLLISION_REQUIRED_FIELDS,
)
from src.verticals.automotive_collision.schemas import (
    AutomotiveCollisionAssessment,
    AutomotiveCollisionIntake,
)


def classify_collision_intake(
    intake: AutomotiveCollisionIntake,
    dynamic_text: str = "",
) -> AutomotiveCollisionAssessment:
    """Classify a Birchwood collision intake with offline deterministic rules."""

    text = _combined_text(intake, dynamic_text)
    flags = _context_flags(intake, text)

    if intake.caller_requested_transfer or _caller_requested_transfer(dynamic_text):
        return _assessment(
            outcome="TRANSFER_COLLISION_CENTER",
            status="transferred",
            result="transferred",
            reason="Caller requested a human transfer.",
            transfer_department="collision_center",
            flags=flags + ["caller_requested_transfer"],
            rules_triggered=["automotive_collision:caller_requested_transfer"],
            confidence=0.96,
        )

    drivable_state = _drivable_state(intake)
    if drivable_state in {"no", "unsure"}:
        reason = (
            "Vehicle is not safe to drive."
            if drivable_state == "no"
            else "Caller is unsure whether the vehicle is safe to drive."
        )
        return _assessment(
            outcome="TRANSFER_COLLISION_CENTER",
            status="transferred",
            result="transferred",
            reason=reason,
            transfer_department="collision_center",
            flags=flags + ["non_drivable_transfer"],
            rules_triggered=["automotive_collision:gate_1_drivability_transfer"],
            confidence=0.95,
        )

    damage = _damage_profile(intake)
    if damage["glass_only"] and not damage["body_damage"]:
        return _assessment(
            outcome="TRANSFER_GLASS_DEPARTMENT",
            status="transferred",
            result="transferred",
            reason="Caller reported glass-only damage.",
            transfer_department="glass_department",
            flags=flags + ["glass_only_transfer"],
            rules_triggered=["automotive_collision:gate_2_glass_only_transfer"],
            confidence=0.94,
        )

    if _is_old_vehicle(intake):
        return _assessment(
            outcome="DECLINED_VEHICLE_YEAR",
            status="declined",
            result="declined",
            reason="Vehicle year is 2011 or older.",
            decline_reason="vehicle_year_2011_or_older",
            flags=flags + ["vehicle_year_declined"],
            rules_triggered=["automotive_collision:gate_3_vehicle_year_declined"],
            confidence=0.97,
        )

    if _vehicle_year_uncertain(intake):
        return _assessment(
            outcome="HUMAN_REVIEW",
            status="human_review",
            result="human_review",
            reason="Vehicle year is missing or unclear.",
            flags=flags + ["callback_needed"],
            missing_information=["vehicle_year"],
            rules_triggered=["automotive_collision:gate_3_vehicle_year_unclear"],
            callback_needed=True,
            human_review_required=True,
            confidence=0.58,
        )

    rebuilt_state = _rebuilt_state(intake)
    if rebuilt_state == "yes":
        return _assessment(
            outcome="DECLINED_REBUILT_SALVAGE",
            status="declined",
            result="declined",
            reason="Vehicle has a rebuilt or salvage title.",
            decline_reason="rebuilt_or_salvage_title",
            flags=flags + ["rebuilt_salvage_declined"],
            rules_triggered=["automotive_collision:gate_4_rebuilt_salvage_declined"],
            confidence=0.97,
        )

    if rebuilt_state == "unsure":
        flags.append("staff_review_rebuilt_status")

    location = _location_decision(intake)
    missing = _missing_information(intake, location)
    private_pay = intake.filing_insurance_claim is False
    if private_pay:
        flags.append("private_pay")
    if _missing_claim_number(intake):
        flags.extend(["missing_claim_number", "callback_needed"])
        missing.append("claim_number")
    if not intake.license_plate:
        flags.extend(["missing_license_plate", "callback_needed"])
        missing.append("license_plate")
    if location["is_luxury"]:
        flags.append("luxury_auto_assigned")
    if location["is_vw"]:
        flags.append("vw_location_choice")

    missing = _dedupe(missing)
    flags = _dedupe(flags)
    callback_needed = "callback_needed" in flags or bool(missing)
    human_review_required = "staff_review_rebuilt_status" in flags

    if _system_uncertainty(intake, damage, rebuilt_state):
        return _assessment(
            outcome="HUMAN_REVIEW",
            status="human_review",
            result="human_review",
            reason="Some intake details were uncertain and need staff review.",
            preferred_collision_center=location["preferred_center"],
            available_collision_centers=location["available_centers"],
            is_luxury=location["is_luxury"],
            is_vw=location["is_vw"],
            private_pay=private_pay,
            flags=flags + ["callback_needed"],
            missing_information=missing,
            rules_triggered=["automotive_collision:human_review_uncertainty"],
            callback_needed=True,
            human_review_required=True,
            confidence=0.56,
        )

    if callback_needed:
        return _assessment(
            outcome="INCOMPLETE_CALLBACK_NEEDED",
            status="incomplete",
            result="incomplete_callback_needed",
            reason="Required follow-up information is missing.",
            preferred_collision_center=location["preferred_center"],
            available_collision_centers=location["available_centers"],
            is_luxury=location["is_luxury"],
            is_vw=location["is_vw"],
            private_pay=private_pay,
            flags=flags,
            missing_information=missing,
            rules_triggered=["automotive_collision:incomplete_callback_needed"],
            callback_needed=True,
            human_review_required=human_review_required,
            confidence=0.72,
        )

    return _assessment(
        outcome="COMPLETED_INTAKE",
        status="completed",
        result="completed_intake",
        reason="All required collision intake details were captured.",
        preferred_collision_center=location["preferred_center"],
        available_collision_centers=location["available_centers"],
        is_luxury=location["is_luxury"],
        is_vw=location["is_vw"],
        private_pay=private_pay,
        flags=flags,
        rules_triggered=["automotive_collision:completed_intake"],
        human_review_required=human_review_required,
        confidence=0.88 if not human_review_required else 0.8,
    )


def configured_collision_locations() -> list[str]:
    """Return demo location placeholders until Birchwood confirms real values."""

    return [
        _clean(getattr(config, "BIRCHWOOD_COLLISION_LOCATION_1", None))
        or BIRCHWOOD_COLLISION_DEFAULT_LOCATIONS[0],
        _clean(getattr(config, "BIRCHWOOD_COLLISION_LOCATION_2", None))
        or BIRCHWOOD_COLLISION_DEFAULT_LOCATIONS[1],
        _clean(getattr(config, "BIRCHWOOD_COLLISION_LOCATION_3", None))
        or BIRCHWOOD_COLLISION_DEFAULT_LOCATIONS[2],
    ]


def configured_luxury_location() -> str:
    """Return demo luxury location placeholder pending stakeholder confirmation."""

    return (
        _clean(getattr(config, "BIRCHWOOD_COLLISION_LUXURY_LOCATION", None))
        or BIRCHWOOD_COLLISION_DEFAULT_LUXURY_LOCATION
    )


def configured_luxury_brands() -> list[str]:
    """Return configurable demo luxury brand list."""

    raw = getattr(config, "BIRCHWOOD_COLLISION_LUXURY_BRANDS", "")
    if isinstance(raw, str) and raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(BIRCHWOOD_COLLISION_DEFAULT_LUXURY_BRANDS)


def is_luxury_make(make: str | None) -> bool:
    normalized_make = _normalize(make or "")
    return any(
        normalized_make == _normalize(brand) for brand in configured_luxury_brands()
    )


def is_vw_make(make: str | None) -> bool:
    normalized_make = _normalize(make or "")
    return normalized_make in {"vw", "volkswagen"}


def parse_intake_bool(value: Any, *, kind: str = "yes_no") -> bool | None:
    """Parse common phone speech booleans without LLM calls."""

    if isinstance(value, bool):
        return value
    text = _normalize(str(value or ""))
    if not text:
        return None
    unsure = ["not sure", "unsure", "unknown", "do not know", "don't know", "maybe"]
    if any(phrase in text for phrase in unsure):
        return None
    if kind == "drivable":
        if _contains_any(
            text,
            [
                "not safe",
                "not drivable",
                "not driveable",
                "cannot drive",
                "can't drive",
                "cant drive",
                "needs a tow",
                "needs towing",
                "tow truck",
                "towed",
                "undrivable",
            ],
        ):
            return False
        if _contains_any(text, ["safe to drive", "drivable", "driveable", "can drive"]):
            return True
    if kind == "insurance":
        if _contains_any(text, ["private pay", "pay myself", "out of pocket"]):
            return False
        if _contains_any(text, ["insurance claim", "filing a claim", "claim number"]):
            return True
    if kind == "rebuilt":
        if _contains_any(text, ["not rebuilt", "not salvage", "clean title", "never"]):
            return False
        if _contains_any(text, ["rebuilt", "salvage", "written off", "write off"]):
            return True
    if re.search(r"\b(no|nope|nah)\b", text):
        return False
    if re.search(r"\b(yes|yeah|yep|correct|sure)\b", text):
        return True
    return None


def parse_vehicle_year(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value or "")
    match = re.search(r"\b(19[0-9]{2}|20[0-9]{2})\b", text)
    if match:
        return int(match.group(1))
    return None


def _assessment(
    *,
    outcome,
    status,
    result,
    reason,
    recommended_routing=None,
    transfer_department=None,
    decline_reason=None,
    preferred_collision_center=None,
    available_collision_centers=None,
    is_luxury=False,
    is_vw=False,
    private_pay=False,
    flags=None,
    missing_information=None,
    rules_triggered=None,
    callback_needed=False,
    human_review_required=False,
    confidence=0.0,
) -> AutomotiveCollisionAssessment:
    return AutomotiveCollisionAssessment(
        outcome=outcome,
        status=status,
        result=result,
        reason=reason,
        recommended_routing=recommended_routing or outcome,
        transfer_department=transfer_department,
        decline_reason=decline_reason,
        preferred_collision_center=preferred_collision_center,
        available_collision_centers=list(available_collision_centers or []),
        is_luxury=is_luxury,
        is_vw=is_vw,
        private_pay=private_pay,
        callback_needed=callback_needed,
        missing_information=_dedupe(list(missing_information or [])),
        flags=_dedupe(list(flags or [])),
        rules_triggered=_dedupe(list(rules_triggered or [])),
        disclaimers_given=list(BIRCHWOOD_COLLISION_DISCLAIMERS),
        confidence=confidence,
        human_review_required=human_review_required,
    )


def _combined_text(intake: AutomotiveCollisionIntake, dynamic_text: str) -> str:
    return _normalize(
        " ".join(
            [
                intake.drivable_raw or "",
                intake.damage_type or "",
                intake.vehicle_make or "",
                intake.vehicle_model or "",
                intake.rebuilt_salvage_raw or "",
                intake.incident_description or "",
                intake.insurance_claim_raw or "",
                dynamic_text or "",
            ]
        )
    )


def _context_flags(intake: AutomotiveCollisionIntake, text: str) -> list[str]:
    flags: list[str] = []
    if intake.multiple_vehicles or _contains_any(
        text,
        ["multiple vehicles", "two vehicles", "several vehicles", "both cars"],
    ):
        flags.append("multiple_vehicles")
    if intake.already_spoke_to_someone or _contains_any(
        text,
        ["already spoke", "spoke to someone", "talked to someone", "called earlier"],
    ):
        flags.append("possible_duplicate")
    return flags


def _drivable_state(intake: AutomotiveCollisionIntake) -> str | None:
    if intake.is_drivable is True:
        return "yes"
    if intake.is_drivable is False:
        return "no"
    raw = _normalize(intake.drivable_raw or "")
    if not raw:
        return None
    parsed = parse_intake_bool(raw, kind="drivable")
    if parsed is True:
        return "yes"
    if parsed is False:
        return "no"
    return "unsure"


def _damage_profile(intake: AutomotiveCollisionIntake) -> dict[str, bool]:
    text = _normalize(intake.damage_type or "")
    glass = intake.glass_only is True or _contains_any(
        text,
        ["glass", "windshield", "windscreen", "window", "rear glass", "side glass"],
    )
    body = intake.body_damage is True or _contains_any(
        text,
        [
            "body",
            "bumper",
            "fender",
            "door",
            "hood",
            "trunk",
            "quarter panel",
            "panel",
            "dent",
            "scratch",
            "paint",
            "frame",
            "front",
            "rear",
            "side",
            "collision",
        ],
    )
    glass_only = bool(glass and not body)
    return {"glass_only": glass_only, "body_damage": bool(body)}


def _is_old_vehicle(intake: AutomotiveCollisionIntake) -> bool:
    return intake.vehicle_year is not None and intake.vehicle_year <= 2011


def _vehicle_year_uncertain(intake: AutomotiveCollisionIntake) -> bool:
    if intake.vehicle_year is not None:
        return False
    raw = _normalize(intake.vehicle_year_raw or "")
    return bool(raw and _contains_any(raw, ["unknown", "not sure", "unsure"]))


def _rebuilt_state(intake: AutomotiveCollisionIntake) -> str | None:
    if intake.is_rebuilt_or_salvage is True:
        return "yes"
    if intake.is_rebuilt_or_salvage is False:
        return "no"
    raw = _normalize(intake.rebuilt_salvage_raw or "")
    if not raw:
        return None
    parsed = parse_intake_bool(raw, kind="rebuilt")
    if parsed is True:
        return "yes"
    if parsed is False:
        return "no"
    return "unsure"


def _location_decision(intake: AutomotiveCollisionIntake) -> dict[str, Any]:
    centers = configured_collision_locations()
    luxury = is_luxury_make(intake.vehicle_make)
    vw = is_vw_make(intake.vehicle_make)
    if luxury:
        return {
            "preferred_center": configured_luxury_location(),
            "available_centers": [configured_luxury_location()],
            "is_luxury": True,
            "is_vw": False,
        }
    return {
        "preferred_center": _clean(intake.preferred_collision_center),
        "available_centers": centers,
        "is_luxury": False,
        "is_vw": vw,
    }


def _missing_information(
    intake: AutomotiveCollisionIntake,
    location: dict[str, Any],
) -> list[str]:
    data = intake.model_dump()
    missing = [
        field
        for field in BIRCHWOOD_COLLISION_REQUIRED_FIELDS
        if _is_missing(data.get(field))
    ]
    if _rebuilt_state(intake) is None:
        missing.append("rebuilt_salvage_status")
    if not location["is_luxury"] and not location["preferred_center"]:
        missing.append("preferred_collision_center")
    return missing


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _missing_claim_number(intake: AutomotiveCollisionIntake) -> bool:
    return intake.filing_insurance_claim is True and not intake.claim_number


def _system_uncertainty(
    intake: AutomotiveCollisionIntake,
    damage: dict[str, bool],
    rebuilt_state: str | None,
) -> bool:
    if rebuilt_state == "unsure":
        return False
    if intake.damage_type and not damage["glass_only"] and not damage["body_damage"]:
        return True
    return False


def _caller_requested_transfer(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in {"0", "zero"} or _contains_any(
        normalized,
        ["press 0", "pressed 0", "operator", "representative", "human please"],
    )


def _contains_any(text: str, phrases: list[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
