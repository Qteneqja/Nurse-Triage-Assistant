"""Deterministic insurance FNOL routing rules."""

from __future__ import annotations

import re
from typing import Any

from src.verticals.insurance.constants import (
    INSURANCE_CLAIMS_FNOL_REQUIRED_FIELDS,
    INSURANCE_DISCLAIMERS,
)
from src.verticals.insurance.schemas import (
    InsuranceClaimAssessment,
    InsuranceClaimIntake,
)


def classify_insurance_claim(
    intake: InsuranceClaimIntake,
    dynamic_text: str = "",
    existing_claim: dict[str, Any] | None = None,
) -> InsuranceClaimAssessment:
    """Classify an insurance FNOL intake with deterministic rules."""

    existing = existing_claim or {}
    text = _combined_text(intake, dynamic_text)
    missing = _missing_required_fields(intake)
    facts = _facts_from_text(text, existing)

    if _information_only_match(text):
        return InsuranceClaimAssessment(
            disposition="INFORMATION_ONLY",
            routing_reason="Caller is asking about the process and does not want to open a claim.",
            recommended_action="Provide general claims-process information and offer a callback.",
            relationship_to_policyholder=_clean(
                existing.get("relationship_to_policyholder")
            ),
            preferred_callback_method=_clean(existing.get("preferred_callback_method"))
            or "phone",
            missing_information=missing,
            rules_triggered=["insurance:information_only"],
            confidence=0.78,
            disclaimers_given=list(INSURANCE_DISCLAIMERS),
        )

    emergency = _emergency_match(text)
    if emergency:
        flag, reason = emergency
        return InsuranceClaimAssessment(
            disposition="EMERGENCY_SERVICES_NOW",
            routing_reason=reason,
            recommended_action=(
                "Advise the caller to contact emergency services immediately, then "
                "route the claim for urgent follow-up."
            ),
            relationship_to_policyholder=_clean(
                existing.get("relationship_to_policyholder")
            ),
            preferred_callback_method=_clean(existing.get("preferred_callback_method"))
            or "phone",
            emergency_or_safety_issue=True,
            injuries_mentioned=facts["injuries_mentioned"],
            emergency_services_involved=True,
            police_or_fire_report=facts["police_or_fire_report"],
            property_secure=False,
            mitigation_needed=True,
            documents_available=facts["documents_available"],
            missing_information=missing,
            rules_triggered=[f"insurance:emergency:{flag}"],
            safety_flags=[flag],
            confidence=0.95,
            human_review_required=True,
            disclaimers_given=list(INSURANCE_DISCLAIMERS),
        )

    if _urgent_adjuster_match(intake, text, facts):
        missing_for_urgent = missing
        return InsuranceClaimAssessment(
            disposition="URGENT_ADJUSTER_REVIEW",
            routing_reason="Potentially urgent claim details need adjuster review.",
            recommended_action="Route to an urgent adjuster queue for same-day review.",
            relationship_to_policyholder=_clean(
                existing.get("relationship_to_policyholder")
            ),
            preferred_callback_method=_clean(existing.get("preferred_callback_method"))
            or "phone",
            emergency_or_safety_issue=facts["emergency_or_safety_issue"],
            injuries_mentioned=facts["injuries_mentioned"],
            emergency_services_involved=facts["emergency_services_involved"],
            police_or_fire_report=facts["police_or_fire_report"],
            property_secure=facts["property_secure"],
            mitigation_needed=facts["mitigation_needed"],
            documents_available=facts["documents_available"],
            missing_information=missing_for_urgent,
            rules_triggered=["insurance:urgent_adjuster_review"],
            safety_flags=_urgent_flags(facts),
            confidence=0.88 if not missing_for_urgent else 0.74,
            human_review_required=bool(missing_for_urgent),
            disclaimers_given=list(INSURANCE_DISCLAIMERS),
        )

    if missing:
        disposition = (
            "DOCUMENTS_NEEDED" if _documentation_gap(text, missing) else "HUMAN_REVIEW"
        )
        return InsuranceClaimAssessment(
            disposition=disposition,
            routing_reason="Required FNOL information is missing or uncertain.",
            recommended_action="Ask an intake specialist to collect missing claim details.",
            relationship_to_policyholder=_clean(
                existing.get("relationship_to_policyholder")
            ),
            preferred_callback_method=_clean(existing.get("preferred_callback_method"))
            or "phone",
            emergency_or_safety_issue=facts["emergency_or_safety_issue"],
            injuries_mentioned=facts["injuries_mentioned"],
            emergency_services_involved=facts["emergency_services_involved"],
            police_or_fire_report=facts["police_or_fire_report"],
            property_secure=facts["property_secure"],
            mitigation_needed=facts["mitigation_needed"],
            documents_available=facts["documents_available"],
            missing_information=missing,
            rules_triggered=[f"insurance:{disposition.lower()}"],
            safety_flags=["missing_required_information"],
            confidence=0.58,
            human_review_required=True,
            disclaimers_given=list(INSURANCE_DISCLAIMERS),
        )

    return InsuranceClaimAssessment(
        disposition="STANDARD_CLAIM_INTAKE",
        routing_reason="Core FNOL fields are present and no urgent safety issue was detected.",
        recommended_action="Create a standard claim intake record for adjuster triage.",
        relationship_to_policyholder=_clean(
            existing.get("relationship_to_policyholder")
        ),
        preferred_callback_method=_clean(existing.get("preferred_callback_method"))
        or "phone",
        emergency_or_safety_issue=facts["emergency_or_safety_issue"],
        injuries_mentioned=facts["injuries_mentioned"],
        emergency_services_involved=facts["emergency_services_involved"],
        police_or_fire_report=facts["police_or_fire_report"],
        property_secure=facts["property_secure"],
        mitigation_needed=facts["mitigation_needed"],
        documents_available=facts["documents_available"],
        missing_information=[],
        rules_triggered=["insurance:standard_claim_intake"],
        confidence=0.82,
        disclaimers_given=list(INSURANCE_DISCLAIMERS),
    )


def should_ask_dynamic_follow_up(intake: InsuranceClaimIntake, user_text: str) -> bool:
    """Return True when the first dynamic turn should ask a claims follow-up."""

    cleaned = _normalize_text(user_text)
    callback = _normalize_text(intake.callback_number or "")
    if not cleaned:
        return True
    if callback and cleaned == callback:
        return True
    return len(cleaned.split()) <= 3


def dynamic_follow_up_question(intake: InsuranceClaimIntake) -> str:
    """Return the deterministic first FNOL follow-up question."""

    claim_type = _normalize_text(intake.claim_type or "")
    if "auto" in claim_type:
        return (
            "Thanks. Was anyone hurt, were emergency services involved, and is the "
            "vehicle still safe to drive?"
        )
    if "water" in claim_type:
        return (
            "Thanks. Is the water stopped, is the property safe, and is any urgent "
            "mitigation needed?"
        )
    if "theft" in claim_type or "loss" in claim_type:
        return (
            "Thanks. Have you filed a police report, and do you have receipts or "
            "photos for the missing items?"
        )
    return (
        "Thanks. Is anyone hurt or in immediate danger, is the property secure, "
        "and do you have photos or documents available?"
    )


def _combined_text(intake: InsuranceClaimIntake, dynamic_text: str) -> str:
    return _normalize_text(
        " ".join(
            [
                intake.claim_type or "",
                intake.incident_summary or "",
                dynamic_text or "",
            ]
        )
    )


def _facts_from_text(text: str, existing: dict[str, Any]) -> dict[str, Any]:
    return {
        "emergency_or_safety_issue": bool(
            existing.get("emergency_or_safety_issue")
            or _contains_any(
                text,
                [
                    "unsafe",
                    "danger",
                    "active fire",
                    "smoke",
                    "emergency",
                    "ems",
                ],
            )
        ),
        "injuries_mentioned": _injuries_mentioned(text, existing),
        "emergency_services_involved": bool(
            existing.get("emergency_services_involved")
            or _contains_any(text, ["police", "fire department", "ems", "ambulance"])
        ),
        "police_or_fire_report": bool(
            existing.get("police_or_fire_report")
            or _contains_any(text, ["police report", "fire report", "filed a report"])
        ),
        "property_secure": _property_secure(text, existing),
        "mitigation_needed": bool(
            existing.get("mitigation_needed")
            or _contains_any(
                text,
                ["mitigation", "water still", "leak stopped", "tarp", "not secure"],
            )
        ),
        "documents_available": bool(
            existing.get("documents_available")
            or _contains_any(text, ["photos", "photo", "receipts", "documents"])
        ),
    }


def _injuries_mentioned(text: str, existing: dict[str, Any]) -> bool:
    if existing.get("injuries_mentioned"):
        return True
    if _contains_any(
        text,
        [
            "no one is hurt",
            "no one hurt",
            "no one was hurt",
            "nobody is hurt",
            "no injuries",
            "not injured",
        ],
    ):
        return False
    return _contains_any(
        text,
        ["hurt", "injured", "injury", "neck pain", "pain", "ambulance", "ems"],
    )


def _property_secure(text: str, existing: dict[str, Any]) -> bool | None:
    if existing.get("property_secure") is not None:
        return bool(existing["property_secure"])
    if _contains_any(
        text, ["property is safe", "property is secure", "opening is secure"]
    ):
        return True
    if _contains_any(text, ["unsafe", "not secure", "active fire", "smoke"]):
        return False
    return None


def _missing_required_fields(intake: InsuranceClaimIntake) -> list[str]:
    data = intake.model_dump()
    return [
        field for field in INSURANCE_CLAIMS_FNOL_REQUIRED_FIELDS if not data.get(field)
    ]


def _information_only_match(text: str) -> bool:
    return _contains_any(
        text,
        [
            "just want to understand",
            "do not want to start a claim",
            "don't want to start a claim",
            "information only",
            "claims process",
            "only wants to know",
        ],
    )


def _emergency_match(text: str) -> tuple[str, str] | None:
    rules = [
        (
            "active_fire",
            ["active fire", "fire right now", "flames", "smoke and unsafe"],
            "Active fire or unsafe scene reported.",
        ),
        (
            "immediate_danger",
            ["immediate danger", "scene is unsafe", "unsafe right now"],
            "Immediate safety danger reported.",
        ),
        (
            "serious_injury",
            ["trapped", "unconscious", "serious injury"],
            "Potential serious injury reported during FNOL intake.",
        ),
    ]
    for flag, phrases, reason in rules:
        if _contains_any(text, phrases):
            return flag, reason
    return None


def _urgent_adjuster_match(
    intake: InsuranceClaimIntake,
    text: str,
    facts: dict[str, Any],
) -> bool:
    claim_type = _normalize_text(intake.claim_type or "")
    if facts["injuries_mentioned"] or facts["emergency_services_involved"]:
        return True
    if "auto" in claim_type and _contains_any(
        text,
        ["not drivable", "another vehicle", "collision", "collided"],
    ):
        return True
    if "water" in claim_type and (
        facts["mitigation_needed"] or _contains_any(text, ["leak", "water damage"])
    ):
        return True
    if facts["property_secure"] is False:
        return True
    return False


def _urgent_flags(facts: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if facts["injuries_mentioned"]:
        flags.append("injuries_mentioned")
    if facts["emergency_services_involved"]:
        flags.append("emergency_services_involved")
    if facts["property_secure"] is False:
        flags.append("property_not_secure")
    if facts["mitigation_needed"]:
        flags.append("mitigation_needed")
    return flags


def _documentation_gap(text: str, missing: list[str]) -> bool:
    return bool(
        {"policy_number", "loss_datetime", "loss_location"}.intersection(missing)
        or _contains_any(text, ["do not have receipts", "no receipts", "not filed"])
    )


def _contains_any(text: str, phrases: list[str]) -> bool:
    for phrase in phrases:
        if len(phrase) <= 3 and phrase.isalnum():
            if re.search(rf"\b{re.escape(phrase)}\b", text):
                return True
            continue
        if phrase in text:
            return True
    return False


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
