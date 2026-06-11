"""Deterministic injury-mention detection for non-clinical verticals.

Invariant 3 (Birchwood pilot): if a caller mentions injuries in a
non-clinical vertical, the system must advise seeking medical attention /
911, flag the record prominently, and never give further medical advice.
This module is the deterministic keyword/pattern layer that decision sits
on — it must never depend on an LLM.

Bias is fail-closed: ambiguous mentions count as mentions. Only explicit
denials ("no one was hurt", "we're all fine") suppress the flag, and a
denial never suppresses a separate positive mention in the same text.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

# Explicit denials are removed from the text before scanning for mentions,
# so "no one was hurt" does not trip the "hurt" pattern. Each pattern must
# consume the injury word it negates.
_DENIAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(?:no(?:body| one|one)?|none of (?:us|them)|neither of us)\s+"
        r"(?:was|were|got|is|are|seems?|appeared)?\s*"
        r"(?:seriously\s+|badly\s+)?(?:hurt|injured)\b",
        r"\bno injur(?:y|ies)\b",
        r"\bnot (?:hurt|injured|bleeding)\b",
        r"\bwasn'?t (?:hurt|injured)\b",
        r"\bweren'?t (?:hurt|injured)\b",
        r"\bdidn'?t get (?:hurt|injured)\b",
        r"\bwithout (?:any )?injur(?:y|ies)\b",
        r"\b(?:everyone|everybody|we|i|he|she|they|the kids?|my \w+)"
        r"(?:'s|'re|'m| is| are| am| was| were)?\s+(?:all\s+|both\s+|totally\s+)?"
        r"(?:fine|okay|ok|alright|all right|unhurt|uninjured|unharmed)\b",
        r"\bluckily,? no(?:body| one)?\b",
        r"\bthankfully,? no(?:body| one)?\b",
    ]
]

# Injury mention patterns. Word boundaries keep "broken windshield" and
# "uninjured" from matching; body-part qualifiers keep "broken" scoped to
# people, not vehicles.
_INJURY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\binjur(?:y|ies|ed)\b",
        r"\bhurt(?:s|ing)?\b",
        r"\bwhiplash\b",
        r"\bbleed(?:s|ing)?\b",
        r"\bblood\b",
        r"\bconcuss(?:ion|ed)\b",
        r"\bdizzy\b|\bdizziness\b",
        r"\bnause(?:a|ous|ated)\b",
        r"\bsore\s+(?:neck|back|shoulder|head|chest|arm|leg|knee)\b",
        r"\b(?:neck|back|head|chest|shoulder|arm|leg|knee|wrist)\s+"
        r"(?:pain|aches?|is sore|hurts?)\b",
        r"\b(?:neck|back|head|chest|shoulder|arm|leg|knee|wrist)\s+"
        r"(?:has been|was|is|feels)\s+(?:sore|stiff|hurting|painful|numb)\b",
        r"\bhit\s+(?:my|his|her|their|the)\s+head\b",
        r"\bambulance\b",
        r"\bparamedics?\b",
        r"\bhospital\b",
        r"\bemergency room\b",
        r"\b911\b|\bnine one one\b",
        r"\bbroken\s+(?:arm|leg|wrist|rib|ribs|bone|bones|collarbone|nose|"
        r"finger|ankle|hand|foot)\b",
        r"\bbruis(?:e|ed|es|ing)\b",
        r"\bstitches\b",
        r"\bpassed out\b|\bunconscious\b|\bblacked? out\b",
        r"\bmedical attention\b",
        r"\bcan'?t move (?:my|his|her|their)\b",
    ]
]


class InjuryScanResult(BaseModel):
    """Outcome of a deterministic injury scan over caller text."""

    mentioned: bool = False
    denied: bool = False
    matched_terms: list[str] = Field(default_factory=list)


# Spoken once per call when an injury mention is detected. This is the full
# extent of medical guidance a non-clinical vertical is allowed to give.
INJURY_SAFETY_ADVISORY = (
    "Most importantly - if anyone is injured or feels unwell, please get "
    "medical attention or call 9 1 1 first. I've flagged that for the team."
)


def scan_for_injuries(text: str | None) -> InjuryScanResult:
    """Scan caller text for injury mentions, honoring explicit denials.

    A denial only suppresses the injury words it directly negates; any
    remaining mention still counts ("my neck hurts but the kids are fine"
    is mentioned=True, denied=True).
    """
    if not text or not text.strip():
        return InjuryScanResult()

    denied = False
    remaining = text
    for pattern in _DENIAL_PATTERNS:
        remaining, count = pattern.subn(" ", remaining)
        if count:
            denied = True

    matched: list[str] = []
    for pattern in _INJURY_PATTERNS:
        for match in pattern.finditer(remaining):
            matched.append(match.group(0).lower())

    return InjuryScanResult(
        mentioned=bool(matched),
        denied=denied,
        matched_terms=sorted(set(matched)),
    )
