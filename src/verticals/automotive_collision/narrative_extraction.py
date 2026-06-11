"""Deterministic narrative extraction for Birchwood collision intake (PR 2).

After the caller tells their story (multi-segment narrative, PR 1), this
module prefills scripted-intake fields the story already answered so the
gap-filling pass only asks for what is genuinely missing. No LLM.

Bias is conservative: a wrong extraction silently skips a question the
caller should have been asked, so each extractor only fires on
high-precision patterns. Anything ambiguous stays unfilled and gets asked.
Every prefilled value carries the source phrase for the decision trace.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from src.safety.injury_detection import scan_for_injuries
from src.verticals.automotive_collision.rules import configured_luxury_brands

# Common makes seen in Manitoba traffic plus the configured luxury brands.
_COMMON_MAKES = [
    "Toyota",
    "Honda",
    "Ford",
    "Chevrolet",
    "Chevy",
    "GMC",
    "Ram",
    "Dodge",
    "Jeep",
    "Chrysler",
    "Hyundai",
    "Kia",
    "Mazda",
    "Nissan",
    "Subaru",
    "Volkswagen",
    "VW",
    "Tesla",
    "Buick",
    "Mitsubishi",
    "Honda",
]

# Words that can follow a make but are never a model.
_MODEL_STOPWORDS = {
    "and",
    "was",
    "is",
    "got",
    "hit",
    "that",
    "the",
    "my",
    "his",
    "her",
    "their",
    "when",
    "while",
    "but",
    "so",
    "in",
    "on",
    "at",
    "it",
    "its",
    "had",
    "has",
    "just",
    "right",
    "with",
    "into",
    "out",
    "off",
    "there",
    "then",
}

# "couldn't drive"/"can't drive" alone is ambiguous ("the other driver
# couldn't drive straight") — require the vehicle as the object/subject.
_NOT_DRIVABLE = re.compile(
    r"(?:not drivable|not driveable|undrivable"
    r"|can'?t (?:be driven|drive it|drive the (?:car|truck|vehicle))"
    r"|cannot (?:be driven|drive it)"
    r"|(?:it|car|truck|vehicle) (?:won'?t|wouldn'?t|doesn'?t) (?:start|run|drive)"
    r"|had (?:it|to be|the car) towed|needs? a tow|tow truck|got towed"
    r"|(?:not|isn'?t) safe to drive)",
    re.IGNORECASE,
)
_DRIVABLE = re.compile(
    r"(?:still drivable|still driveable|drove (?:it )?(?:home|away|here)"
    r"|still (?:runs and )?drives|drives fine|safe to drive|still driving it)",
    re.IGNORECASE,
)

_DATETIME = re.compile(
    r"\b(yesterday(?: morning| afternoon| evening)?|today|this morning"
    r"|this afternoon|this evening|last night|last week|over the weekend"
    r"|last (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?: morning| afternoon| evening| night)?"
    r"|(?:a couple of|a few|two|three|four|five) days ago"
    r"|(?:january|february|march|april|may|june|july|august|september"
    r"|october|november|december) \d{1,2}(?:st|nd|rd|th)?)\b",
    re.IGNORECASE,
)

_LOCATION = re.compile(
    r"\b(?:at|near) ((?:[a-z0-9']+ ){0,2}?[a-z0-9']+ and (?:[a-z0-9']+ ){0,2}?[a-z0-9']+)(?=[,.;]|\s|$)"
    r"|\bon (the perimeter|the highway|highway \d+|route \d+"
    r"|[a-z']+ (?:street|avenue|ave|road|rd|drive|boulevard|blvd|crescent|highway|hwy))\b"
    r"|\bin (?:a |the )?(parking (?:lot|garage)|driveway|alley|garage)\b",
    re.IGNORECASE,
)

_NO_POLICE = re.compile(
    r"(?:didn'?t (?:call|involve) the police|no police report|police (?:were|was)n'?t"
    r"|haven'?t (?:filed|called) (?:a |the )?police)",
    re.IGNORECASE,
)
_POLICE = re.compile(
    r"(?:police (?:came|showed up|attended|took)|officer (?:came|took|wrote)"
    r"|(?:filed|made|got) a police report|police report number)",
    re.IGNORECASE,
)

_PHOTOS = re.compile(
    r"(?:took (?:some |a few |a bunch of )?(?:photos|pictures)"
    r"|have (?:photos|pictures) of|photos of the damage)",
    re.IGNORECASE,
)

_HIT_AND_RUN = re.compile(r"hit and run|hit-and-run|(?:they|driver) took off", re.I)
_OTHER_PARTY = re.compile(
    r"(?:the other (?:driver|car|vehicle|guy|lady)|another (?:car|vehicle|driver)"
    r"|two (?:cars|vehicles)|both (?:cars|vehicles))",
    re.IGNORECASE,
)
_SINGLE_VEHICLE = re.compile(
    r"(?:no (?:other|one else)|nobody else|just (?:me|my car)|single vehicle"
    r"|hit a (?:deer|moose|pole|post|tree|wall|curb|median))",
    re.IGNORECASE,
)

_PRIVATE_PAY = re.compile(
    r"(?:pay(?:ing)? (?:for it )?(?:myself|out of pocket)|out of pocket"
    r"|private pay|not going through insurance|won'?t be (?:using|going through) insurance)",
    re.IGNORECASE,
)
_FILING_CLAIM = re.compile(
    r"(?:going through (?:insurance|mpi|autopac)|filed? a claim|opened a claim"
    r"|claim number|through my insurance|insurance is (?:covering|handling))",
    re.IGNORECASE,
)

_PROVIDERS = [
    ("MPI", re.compile(r"\b(?:mpi|manitoba public insurance|autopac)\b", re.I)),
    ("Intact", re.compile(r"\bintact\b", re.I)),
    ("Wawanesa", re.compile(r"\bwawanesa\b", re.I)),
    ("Aviva", re.compile(r"\baviva\b", re.I)),
    ("TD Insurance", re.compile(r"\btd insurance\b", re.I)),
    ("SGI", re.compile(r"\bsgi\b", re.I)),
    ("ICBC", re.compile(r"\bicbc\b", re.I)),
]

_CLAIM_NUMBER = re.compile(
    r"claim (?:number|#)?(?: is)?\s*[:#]?\s*([a-z]{0,4}[- ]?\d[\d\- ]{3,}\d)",
    re.IGNORECASE,
)

_DAMAGE_KEYWORDS = [
    "windshield",
    "window",
    "glass",
    "bumper",
    "fender",
    "door",
    "hood",
    "trunk",
    "quarter panel",
    "panel",
    "headlight",
    "taillight",
    "mirror",
    "roof",
    "wheel",
    "frame",
    "front end",
    "rear end",
    "tailgate",
    "grille",
]


class NarrativePrefill(BaseModel):
    """Field values confidently extracted from a collision narrative."""

    fields: dict[str, Any] = Field(default_factory=dict)
    audit: list[dict[str, str]] = Field(default_factory=list)


def extract_from_narrative(narrative: str | None) -> NarrativePrefill:
    """Prefill scripted-intake fields from the caller's story."""
    result = NarrativePrefill()
    if not narrative or not narrative.strip():
        return result
    text = narrative.strip()

    def fill(field: str, value: Any, source: str) -> None:
        result.fields[field] = value
        result.audit.append(
            {"field": field, "value": str(value), "source": source[:120]}
        )

    # Drivability — negative patterns win (fail toward the transfer gate).
    m = _NOT_DRIVABLE.search(text)
    if m:
        fill("is_drivable", False, m.group(0))
        fill("drivable_raw", m.group(0), m.group(0))
    else:
        m = _DRIVABLE.search(text)
        if m:
            fill("is_drivable", True, m.group(0))
            fill("drivable_raw", m.group(0), m.group(0))

    # Vehicle year + make + model — year only counts when tied to a make
    # ("my 2021 Toyota Corolla"), never a bare number in the story.
    make, model, year, source = _vehicle_from_text(text)
    if make:
        fill("vehicle_make", make, source)
    if model:
        fill("vehicle_model", model, source)
    if year:
        fill("vehicle_year", year, source)

    # Damage keywords.
    damage_hits = [k for k in _DAMAGE_KEYWORDS if k in text.lower()]
    if damage_hits:
        fill(
            "damage_type", ", ".join(dict.fromkeys(damage_hits)), ", ".join(damage_hits)
        )

    # When / where.
    m = _DATETIME.search(text)
    if m:
        fill("incident_datetime", m.group(0), m.group(0))
    m = _LOCATION.search(text)
    if m:
        fill("incident_location", m.group(0), m.group(0))

    # Police report / photos / other parties.
    m = _NO_POLICE.search(text)
    if m:
        fill("police_report_filed", "no", m.group(0))
    else:
        m = _POLICE.search(text)
        if m:
            fill("police_report_filed", "yes", m.group(0))
    m = _PHOTOS.search(text)
    if m:
        fill("photos_available", "yes", m.group(0))
    m = _HIT_AND_RUN.search(text)
    if m:
        fill("other_parties", "hit and run - other party left the scene", m.group(0))
    else:
        m = _OTHER_PARTY.search(text)
        if m:
            fill("other_parties", "another vehicle involved", m.group(0))
        else:
            m = _SINGLE_VEHICLE.search(text)
            if m:
                fill("other_parties", "single vehicle", m.group(0))

    # Insurance — negation checked first ("not going through insurance").
    m = _PRIVATE_PAY.search(text)
    if m:
        fill("filing_insurance_claim", False, m.group(0))
        fill("insurance_claim_raw", m.group(0), m.group(0))
        fill("insurance_provider", "private pay", m.group(0))
    else:
        m = _FILING_CLAIM.search(text)
        if m:
            fill("filing_insurance_claim", True, m.group(0))
            fill("insurance_claim_raw", m.group(0), m.group(0))
    for provider, pattern in _PROVIDERS:
        m = pattern.search(text)
        if m:
            fill("insurance_provider", provider, m.group(0))
            break
    m = _CLAIM_NUMBER.search(text)
    if m:
        fill("claim_number", m.group(1).strip(), m.group(0))

    # Injuries — Invariant 3 state, reused everywhere.
    scan = scan_for_injuries(text)
    if scan.mentioned:
        fill("injuries_state", "reported", "; ".join(scan.matched_terms))
    elif scan.denied:
        fill("injuries_state", "denied", "explicit denial in narrative")

    return result


def _vehicle_from_text(
    text: str,
) -> tuple[str | None, str | None, int | None, str]:
    makes = list(dict.fromkeys(_COMMON_MAKES + configured_luxury_brands()))
    make_alt = "|".join(re.escape(m) for m in makes)
    pattern = re.compile(
        rf"\b(?:(19[89]\d|20[0-2]\d)\s+(?:[a-z]+\s+)??)?({make_alt})\b(?:\s+([a-z0-9][a-z0-9-]+))?",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None, None, None, ""
    year = int(m.group(1)) if m.group(1) else None
    raw_make = m.group(2)
    make = _canonical_make(raw_make, makes)
    model_token = (m.group(3) or "").strip()
    model = None
    if model_token and model_token.lower() not in _MODEL_STOPWORDS:
        model = model_token.upper() if len(model_token) <= 3 else model_token.title()
    return make, model, year, m.group(0)


def _canonical_make(raw: str, makes: list[str]) -> str:
    lowered = raw.lower()
    if lowered == "chevy":
        return "Chevrolet"
    if lowered == "vw":
        return "Volkswagen"
    for make in makes:
        if make.lower() == lowered:
            return make
    return raw.title()
