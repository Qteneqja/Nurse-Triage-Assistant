"""Deterministic hazard / scene / legal escalation for Birchwood collision intake.

PROVISIONAL — confirm the trigger lists with Birchwood (July discovery call).

Mirrors src/safety/injury_detection.py: a deterministic regex/keyword layer (no
LLM, ever) that flags safety- or legal-sensitive situations a collision-intake
bot must NOT handle routinely. When triggered, the workflow routes to
ESCALATE_SAFETY (an immediate human handoff with safety guidance) instead of the
normal booking/transfer/decline gates.

Bias is fail-closed and over-escalation: ambiguous matches escalate, and the
caller (rules.py) treats a scan exception as triggered. This is additive — the
existing injury branch (Invariant 3) is unchanged; injury keeps its own overlay.

Categories:
- hazard: fire/smoke, fuel or gas leak, deployed airbags, an unsafe position in
  live traffic — the caller may be in physical danger right now.
- scene: the crash is active/just happened, someone is trapped, or the caller is
  in acute distress — prioritize safety and a human, not data collection.
- legal: disputed liability, legal threat/lawyer, a fatality, or a police/
  criminal proceeding — no advice; hand to a human immediately.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

# (rule_suffix, compiled_pattern) grouped by category. Patterns are scoped to
# avoid tripping on routine collision narration ("rear-ended", "front end
# crushed", "police came and took a report") — see the negative tests.
_HAZARD: list[tuple[str, re.Pattern[str]]] = [
    (
        "fire",
        re.compile(r"\b(?:on fire|caught fire|catching fire|flames|burning)\b", re.I),
    ),
    (
        "smoke",
        re.compile(
            r"\b(?:smoke|smoking)\b|\bsmell(?:s|ing)?\s+(?:of\s+)?(?:gas|fuel|gasoline|burning)\b",
            re.I,
        ),
    ),
    (
        "fuel_leak",
        re.compile(
            r"\b(?:fuel|gas|gasoline)\s+(?:is\s+)?(?:leak\w*|spill\w*|pouring)\b"
            r"|\bleaking\s+(?:fuel|gas|gasoline)\b|\b(?:gas|fuel)\s+leak\b",
            re.I,
        ),
    ),
    (
        "airbag",
        re.compile(
            r"\bairbags?\b(?:[^.]{0,30})?\b(?:deploy\w*|went off|popped|blew)\b"
            r"|\bdeployed\s+airbags?\b",
            re.I,
        ),
    ),
    (
        "roadway",
        re.compile(
            r"\bmiddle of the (?:road|highway|freeway|intersection)\b"
            r"|\bblocking (?:traffic|the road|the lane)\b|\bin (?:a |the )?live lane\b"
            r"|\boncoming traffic\b|\bcan'?t get (?:it )?off the road\b",
            re.I,
        ),
    ),
]

_SCENE: list[tuple[str, re.Pattern[str]]] = [
    (
        "active_scene",
        re.compile(
            r"\b(?:still |right )?(?:at|on)\s+the\s+scene\b|\bit just happened\b"
            r"|\bhappening (?:right )?now\b|\bjust got (?:hit|rear-?ended|in an accident)\b"
            r"|\bjust crashed\b",
            re.I,
        ),
    ),
    (
        "trapped",
        re.compile(
            r"\btrapped\b|\bstuck in the car\b|\bcan'?t get out\b|\bpinned\b", re.I
        ),
    ),
    (
        "distress",
        re.compile(
            r"\bi'?m\s+(?:scared|terrified|panicking|freaking out|shaking|really shaken|in shock)\b"
            r"|\bplease help\b|\bi don'?t know what to do\b",
            re.I,
        ),
    ),
]

_LEGAL: list[tuple[str, re.Pattern[str]]] = [
    (
        "disputed_liability",
        re.compile(
            r"\bdisput\w*\b|\bcontest\w*\b|\bblaming me\b|\bsays it'?s my fault\b"
            r"|\bwho(?:'?s| is)\s+at fault\b|\bwon'?t admit (?:fault|it)\b|\bliab(?:le|ility)\b",
            re.I,
        ),
    ),
    (
        "legal_threat",
        re.compile(
            r"\blawyer\b|\battorney\b|\bsu(?:e|ing)\b|\blawsuit\b|\blitigation\b"
            r"|\blegal action\b|\bpress(?:ing)? charges\b|\btake (?:them|me|this) to court\b",
            re.I,
        ),
    ),
    (
        "fatality",
        re.compile(
            r"\b(?:someone|somebody|a person|the (?:driver|passenger|pedestrian))\s+"
            r"(?:died|passed away|was killed)\b|\bfatal(?:ity|ities)?\b|\bdeceased\b"
            r"|\bsomeone (?:was )?killed\b",
            re.I,
        ),
    ),
    (
        "police_proceeding",
        re.compile(
            r"\bunder investigation\b|\bcriminal\b|\bcharged with\b|\bhit and run\b"
            r"|\bimpaired driv\w*\b|\bdrunk driv\w*\b|\bdui\b",
            re.I,
        ),
    ),
]

_ALL: list[tuple[str, str, re.Pattern[str]]] = (
    [("hazard", suffix, pat) for suffix, pat in _HAZARD]
    + [("scene", suffix, pat) for suffix, pat in _SCENE]
    + [("legal", suffix, pat) for suffix, pat in _LEGAL]
)

RULE_ID_PREFIX = "automotive_collision"

# Spoken once when a safety escalation fires. Mirrors the injury advisory: this
# is the FULL extent of guidance the bot gives — then a human takes over.
SAFETY_ESCALATION_ADVISORY = (
    "Your safety comes first. If anyone is in danger, get to a safe place and "
    "call 9 1 1 right away. I'm connecting you with a member of our team now."
)


class CollisionSafetyScan(BaseModel):
    """Deterministic hazard/scene/legal scan over caller text."""

    triggered: bool = False
    categories: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)


def scan_collision_safety(text: str | None) -> CollisionSafetyScan:
    """Scan caller text for hazard/scene/legal escalation triggers.

    Returns a CollisionSafetyScan; triggered=True if any category matched.
    Deterministic and side-effect free. Callers should treat an exception as
    triggered (fail-closed) — see rules.classify_collision_intake.
    """
    if not text or not text.strip():
        return CollisionSafetyScan()

    categories: list[str] = []
    rule_ids: list[str] = []
    matched: list[str] = []
    for category, suffix, pattern in _ALL:
        m = pattern.search(text)
        if m:
            if category not in categories:
                categories.append(category)
            rule_ids.append(f"{RULE_ID_PREFIX}:{category}:{suffix}")
            matched.append(m.group(0).lower().strip())

    return CollisionSafetyScan(
        triggered=bool(rule_ids),
        categories=categories,
        rule_ids=rule_ids,
        matched_terms=sorted(set(matched)),
    )
