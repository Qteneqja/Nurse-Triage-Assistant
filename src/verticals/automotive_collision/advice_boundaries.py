"""Restricted-advice boundaries for Birchwood collision intake (vertical post-check).

PROVISIONAL — confirm the boundary list with Birchwood (July discovery call).

The intake bot must NEVER state insurance coverage, assign fault/liability, quote
a repair cost/estimate, or give legal/medical advice. Two deterministic layers
(no LLM):

1. ``scan_restricted_advice(text)`` / ``enforce_advice_boundaries(text)`` — a
   post-check over ASSISTANT/LLM output. If a caller-facing line states restricted
   advice, it is discarded and replaced with a safe disclaimer, and the caller is
   escalated. The Birchwood flow is deterministic today, so this is primarily a
   guardrail over our own templated text and a safety net for any future LLM text;
   it composes with the platform safety gate (src/safety/gate.py).
2. ``detect_advice_request(text)`` — detects a CALLER *asking* for restricted
   advice ("will my insurance cover this?", "how much will it cost?"). rules.py
   uses this to flag the record and route to HUMAN_REVIEW so staff — not the bot —
   handle the question.

Patterns target AFFIRMATIVE statements/requests only; the existing disclaimers
("does not provide ... coverage decisions", "doesn't confirm coverage, pricing")
and routine intake text ("going through insurance") must pass clean — enforced by
test_birchwood_safety_escalation.py.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

# ── ASSISTANT output: affirmative restricted-advice statements ──────────────
_OUTPUT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "coverage",
        re.compile(
            r"\b(?:is|are|will be|you'?re|it'?s|that'?s|you are)\s+covered\b"
            r"|\bwe(?:'?ll| will)\s+cover\b|\binsurance will pay\b"
            r"|\b(?:your )?claim (?:is|will be|has been)\s+approved\b"
            r"|\bcovered under (?:your )?policy\b",
            re.I,
        ),
    ),
    (
        "fault",
        re.compile(
            r"\byou(?:'?re| are)\s+(?:at fault|liable|responsible for)\b"
            r"|\bit(?:'?s| is| was)\s+(?:your|their|his|her|the other driver'?s)\s+fault\b"
            r"|\b(?:the other driver|they)\s+(?:is|are|was|were)\s+at fault\b"
            r"|\bnot (?:your|their) fault\b",
            re.I,
        ),
    ),
    (
        "cost",
        re.compile(
            r"\$\s?\d|\b\d+\s*dollars\b"
            r"|\b(?:cost|estimate|repair|it'?ll|it will|that'?ll)\s+(?:is|will be|of|around|about|come to|comes to)?\s*\$?\d"
            r"|\bthe estimate (?:is|will be|comes to|of)\b",
            re.I,
        ),
    ),
    (
        "legal",
        re.compile(
            r"\byou should\s+(?:sue|get a lawyer|hire (?:a|an) (?:lawyer|attorney)|file a lawsuit)\b"
            r"|\byou can sue\b|\byou should press charges\b",
            re.I,
        ),
    ),
    (
        "medical",
        re.compile(
            r"\byou (?:have|might have|probably have|may have)\s+(?:a |an )?"
            r"(?:concussion|whiplash|broken \w+|fracture|sprain)\b"
            r"|\bit'?s (?:just |only )?(?:whiplash|a concussion|a sprain)\b"
            r"|\byou'?re fine\b|\byou'?ll be fine\b"
            r"|\byou don'?t need (?:a doctor|to see a doctor|medical attention)\b"
            r"|\btake (?:some )?(?:advil|tylenol|ibuprofen|painkillers)\b",
            re.I,
        ),
    ),
]

# ── CALLER input: requests for restricted advice ────────────────────────────
_REQUEST_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "coverage",
        re.compile(
            r"\b(?:will|does|is|do)\s+(?:my\s+|the\s+)?(?:insurance|policy|claim|it|this|that|the damage)\s+"
            r"(?:going to\s+)?cover\b"
            r"|\bis (?:this|it|that|the damage|my car)\s+covered\b|\bam i covered\b"
            r"|\bdo i have coverage\b",
            re.I,
        ),
    ),
    (
        "cost",
        re.compile(
            r"\bhow much (?:will|does|would|is)\b[^?]*\b(?:cost|be|repair|fix|charge)\b"
            r"|\bwhat(?:'?s| is| will)\b[^?]*\b(?:cost|estimate|price|charge)\b"
            r"|\bhow much to (?:fix|repair)\b|\bwhat'?s the (?:estimate|damage going to cost)\b"
            r"|\bgive me (?:an? )?(?:estimate|ballpark|quote)\b",
            re.I,
        ),
    ),
    (
        "fault",
        re.compile(
            r"\bwho(?:'?s| is)\s+at fault\b|\bam i (?:at fault|liable|responsible)\b"
            r"|\bwhose fault\b|\bis it my fault\b|\bwho'?s to blame\b",
            re.I,
        ),
    ),
    (
        "legal",
        re.compile(
            r"\bshould i (?:sue|get a lawyer|call a lawyer)\b|\bdo i need a lawyer\b"
            r"|\bcan i sue\b|\bshould i press charges\b",
            re.I,
        ),
    ),
    (
        "medical",
        re.compile(
            r"\bshould i (?:see|go to) (?:a |the )?(?:doctor|hospital|er)\b"
            r"|\bdo i need (?:a doctor|to see a doctor|medical attention)\b",
            re.I,
        ),
    ),
]

# Safe replacement spoken when an assistant line is discarded for stating
# restricted advice. Points the caller to the right authority and never
# affirms coverage/fault/cost.
RESTRICTED_ADVICE_SAFE_REPLY = (
    "I'm not able to speak to coverage, fault, or repair costs - a Birchwood "
    "advisor will go over those with you. Let me make sure the team has your "
    "details so they can follow up."
)


class AdviceBoundaryResult(BaseModel):
    """Outcome of enforcing restricted-advice boundaries over assistant text."""

    safe_text: str
    violations: list[str] = Field(default_factory=list)
    escalate: bool = False


def scan_restricted_advice(text: str | None) -> list[str]:
    """Return the restricted-advice categories an ASSISTANT line states, if any."""
    if not text or not text.strip():
        return []
    found: list[str] = []
    for category, pattern in _OUTPUT_PATTERNS:
        if pattern.search(text) and category not in found:
            found.append(category)
    return found


def enforce_advice_boundaries(text: str | None) -> AdviceBoundaryResult:
    """Post-check assistant text: discard + replace + escalate on violation.

    Clean text passes through unchanged (no-op for the deterministic templates).
    """
    if not text:
        return AdviceBoundaryResult(safe_text=text or "", violations=[], escalate=False)
    violations = scan_restricted_advice(text)
    if violations:
        return AdviceBoundaryResult(
            safe_text=RESTRICTED_ADVICE_SAFE_REPLY,
            violations=violations,
            escalate=True,
        )
    return AdviceBoundaryResult(safe_text=text, violations=[], escalate=False)


def detect_advice_request(text: str | None) -> list[str]:
    """Return the restricted-advice categories a CALLER is asking about, if any."""
    if not text or not text.strip():
        return []
    found: list[str] = []
    for category, pattern in _REQUEST_PATTERNS:
        if pattern.search(text) and category not in found:
            found.append(category)
    return found
