"""
"Never Diagnose" Enforcement Layer — Phase 1 Hardening

Deterministic enforcement that scans LLM responses for diagnostic claims,
rewrites them to safe phrasing, and logs all rewrite events.

This module provides:
- DIAGNOSTIC_BLOCKLIST: regex + keyword patterns for diagnosis detection
- enforce_no_diagnosis(): scan and rewrite function
- DiagnosisRewriteEvent: structured audit log of rewrites
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Diagnosis rewrite event (for audit trail)
# ---------------------------------------------------------------------------


@dataclass
class DiagnosisRewriteEvent:
    """Structured record of a diagnosis rewrite for audit logging."""

    original_text: str
    rewritten_text: str
    pattern_matched: str
    rule_id: str


# ---------------------------------------------------------------------------
# Diagnostic pattern blocklist
# ---------------------------------------------------------------------------

# Each entry: (rule_id, compiled_regex, replacement_template)
# The replacement uses a safe phrasing that avoids diagnostic claims.

_DIAGNOSTIC_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    # "You have [condition]"
    (
        "DIAG_YOU_HAVE",
        re.compile(
            r"\byou\s+(have|are\s+suffering\s+from|are\s+experiencing)\s+"
            r"(?:a\s+|an\s+)?"
            r"([\w\s]+(?:disease|condition|syndrome|disorder|illness|infection|"
            r"cancer|tumor|fracture|appendicitis|pneumonia|bronchitis|"
            r"diabetes|hypertension|arrhythmia|infarction|embolism|"
            r"meningitis|sepsis|cellulitis|aneurysm))",
            re.IGNORECASE,
        ),
        "your symptoms may be consistent with a condition that requires medical evaluation",
    ),
    # "This is [diagnosis]"
    (
        "DIAG_THIS_IS",
        re.compile(
            r"\bthis\s+is\s+(?:likely\s+)?(?:a\s+)?(?:case\s+of\s+|sign\s+of\s+)?"
            r"([\w\s]+(?:disease|condition|syndrome|disorder|illness|infection|"
            r"cancer|tumor|appendicitis|pneumonia|bronchitis|diabetes|"
            r"hypertension|arrhythmia|infarction|meningitis|sepsis))",
            re.IGNORECASE,
        ),
        "these symptoms suggest you should be evaluated by a medical professional",
    ),
    # "I diagnose / diagnosing / diagnosed"
    (
        "DIAG_EXPLICIT",
        re.compile(r"\b(?:I\s+)?diagnos(?:e|ed|ing|is)\b", re.IGNORECASE),
        "assessment suggests",
    ),
    # "The cause is / appears to be"
    (
        "DIAG_CAUSE_IS",
        re.compile(
            r"\bthe\s+cause\s+(?:is|appears\s+to\s+be|seems\s+to\s+be)\s+[\w\s]+",
            re.IGNORECASE,
        ),
        "the possible cause should be determined by a medical professional",
    ),
    # "You definitely have"
    (
        "DIAG_DEFINITIVE",
        re.compile(
            r"\byou\s+(?:definitely|certainly|clearly|obviously)\s+have\b",
            re.IGNORECASE,
        ),
        "your symptoms suggest you should seek medical evaluation for",
    ),
    # "It is [disease name]" (common standalone diagnosis)
    (
        "DIAG_IT_IS",
        re.compile(
            r"\bit\s+(?:is|looks\s+like|sounds\s+like)\s+(?:a\s+|an\s+)?"
            r"(appendicitis|pneumonia|heart\s+attack|stroke|cancer|tumor|"
            r"fracture|infection|sepsis|meningitis|embolism|aneurysm|"
            r"diabetes|flu|covid|bronchitis)",
            re.IGNORECASE,
        ),
        "your symptoms are consistent with a condition that needs professional evaluation",
    ),
    # "You are having a [medical event]"
    (
        "DIAG_HAVING",
        re.compile(
            r"\byou\s+are\s+(?:probably\s+)?having\s+(?:a\s+|an\s+)?"
            r"(heart\s+attack|stroke|seizure|aneurysm|embolism|"
            r"miscarriage|appendicitis|panic\s+attack)",
            re.IGNORECASE,
        ),
        "your symptoms may indicate a serious condition — please seek immediate medical care",
    ),
]

# Keyword blocklist — standalone medical terms that should not appear as diagnosis
DIAGNOSTIC_KEYWORD_BLOCKLIST = frozenset(
    {
        "diagnosed",
        "diagnosis",
        "prognosis",
        "you have cancer",
        "you have diabetes",
        "you have pneumonia",
        "you have an infection",
        "confirmed diagnosis",
        "clinical diagnosis",
        "differential diagnosis",
    }
)


# ---------------------------------------------------------------------------
# Enforcement function
# ---------------------------------------------------------------------------


def enforce_no_diagnosis(text: str) -> Tuple[str, List[DiagnosisRewriteEvent]]:
    """Scan text for diagnostic claims and rewrite to safe phrasing.

    Args:
        text: LLM-generated text to scan.

    Returns:
        Tuple of (cleaned_text, list_of_rewrite_events).
        If no rewrites needed, returns (original_text, []).
    """
    if not text or not text.strip():
        return text, []

    events: List[DiagnosisRewriteEvent] = []
    cleaned = text

    # Pattern-based scanning and rewriting
    for rule_id, pattern, replacement in _DIAGNOSTIC_PATTERNS:
        match = pattern.search(cleaned)
        while match:
            original_span = match.group(0)
            cleaned = cleaned[: match.start()] + replacement + cleaned[match.end() :]

            events.append(
                DiagnosisRewriteEvent(
                    original_text=original_span,
                    rewritten_text=replacement,
                    pattern_matched=pattern.pattern[:80],
                    rule_id=rule_id,
                )
            )

            logger.warning(
                f"[DIAG_ENFORCE] Rewrote diagnostic claim: "
                f"'{original_span[:60]}' → '{replacement[:60]}' "
                f"(rule={rule_id})"
            )

            # Re-search from the beginning after replacement
            match = pattern.search(cleaned)

    # Keyword blocklist scan (log but replace with generic)
    text_lower = cleaned.lower()
    for keyword in DIAGNOSTIC_KEYWORD_BLOCKLIST:
        if keyword in text_lower:
            # Replace case-insensitively
            kw_pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            kw_match = kw_pattern.search(cleaned)
            if kw_match:
                original_span = kw_match.group(0)
                safe_replacement = "clinical assessment"
                cleaned = kw_pattern.sub(safe_replacement, cleaned)
                events.append(
                    DiagnosisRewriteEvent(
                        original_text=original_span,
                        rewritten_text=safe_replacement,
                        pattern_matched=f"keyword_blocklist:{keyword}",
                        rule_id="KEYWORD_BLOCKLIST",
                    )
                )
                logger.warning(
                    f"[DIAG_ENFORCE] Blocked keyword: "
                    f"'{original_span}' → '{safe_replacement}'"
                )

    return cleaned, events
