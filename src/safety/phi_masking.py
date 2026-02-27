"""
PHI Masking Enforcement — Phase 3 Hardening

Automatic masking of Protected Health Information (PHI) in logs,
transcripts, and stored data when STORE_PHI=False.

Implements deterministic regex-based masking for:
- Names (contextual patterns)
- Phone numbers
- SSN
- Email addresses
- Dates of birth
- Addresses (street patterns)
- Medical record numbers

All masking is irreversible — original data cannot be recovered from masked output.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Mask token
_MASK = "[REDACTED]"
_MASK_NAME = "[REDACTED_NAME]"
_MASK_PHONE = "[REDACTED_PHONE]"
_MASK_SSN = "[REDACTED_SSN]"
_MASK_EMAIL = "[REDACTED_EMAIL]"
_MASK_DOB = "[REDACTED_DOB]"
_MASK_ADDRESS = "[REDACTED_ADDRESS]"
_MASK_MRN = "[REDACTED_MRN]"


# ---------------------------------------------------------------------------
# PHI detection patterns (compiled at module load)
# ---------------------------------------------------------------------------

_PHI_PATTERNS: List[tuple] = [
    # SSN: 123-45-6789 or 123 45 6789
    ("ssn", re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"), _MASK_SSN),

    # Phone numbers: various US formats
    ("phone", re.compile(
        r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
    ), _MASK_PHONE),

    # Email addresses
    ("email", re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    ), _MASK_EMAIL),

    # Date of birth patterns: MM/DD/YYYY, MM-DD-YYYY, YYYY-MM-DD
    ("dob", re.compile(
        r"\b(?:(?:0[1-9]|1[0-2])[/\-](?:0[1-9]|[12]\d|3[01])[/\-](?:19|20)\d{2}|"
        r"(?:19|20)\d{2}[/\-](?:0[1-9]|1[0-2])[/\-](?:0[1-9]|[12]\d|3[01]))\b"
    ), _MASK_DOB),

    # Street addresses (e.g., "123 Main Street", "456 Oak Ave")
    ("address", re.compile(
        r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+)+"
        r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|"
        r"Road|Rd|Circle|Cir|Court|Ct|Way|Place|Pl)\b",
        re.IGNORECASE,
    ), _MASK_ADDRESS),

    # Medical record numbers (MRN patterns: MRN followed by digits)
    ("mrn", re.compile(
        r"\b(?:MRN|mrn|Medical\s+Record)\s*[:#]?\s*\d{4,12}\b",
        re.IGNORECASE,
    ), _MASK_MRN),

    # Names after "my name is" / "I am" / "name:" patterns
    ("name_context", re.compile(
        r"(?:my\s+name\s+is|i\s+am|name\s*:\s*|patient\s*:\s*)"
        r"\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
        re.IGNORECASE,
    ), _MASK_NAME),
]


# ---------------------------------------------------------------------------
# Core masking function
# ---------------------------------------------------------------------------

def mask_phi(text: str) -> str:
    """Mask all detected PHI in the given text.

    Applies all PHI detection patterns. Masking is irreversible.

    Args:
        text: Input text that may contain PHI.

    Returns:
        Text with all detected PHI replaced by mask tokens.
    """
    if not text:
        return text

    masked = text
    for pattern_name, pattern, mask_token in _PHI_PATTERNS:
        masked = pattern.sub(mask_token, masked)

    return masked


def mask_phi_in_dict(data: dict, keys_to_mask: Optional[List[str]] = None) -> dict:
    """Mask PHI in specified dict keys (or all string values).

    Args:
        data: Dictionary potentially containing PHI.
        keys_to_mask: If specified, only mask values for these keys.
                      If None, mask all string values.

    Returns:
        New dict with PHI masked.
    """
    result = {}
    for key, value in data.items():
        if keys_to_mask and key not in keys_to_mask:
            result[key] = value
        elif isinstance(value, str):
            result[key] = mask_phi(value)
        elif isinstance(value, dict):
            result[key] = mask_phi_in_dict(value, keys_to_mask)
        elif isinstance(value, list):
            result[key] = [
                mask_phi(item) if isinstance(item, str) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def mask_transcript(transcript: List[Dict]) -> List[Dict]:
    """Mask PHI in a conversation transcript.

    Args:
        transcript: List of conversation turn dicts with 'text' key.

    Returns:
        New transcript list with PHI masked in all text fields.
    """
    masked = []
    for turn in transcript:
        masked_turn = dict(turn)
        if "text" in masked_turn and isinstance(masked_turn["text"], str):
            masked_turn["text"] = mask_phi(masked_turn["text"])
        if "content" in masked_turn and isinstance(masked_turn["content"], str):
            masked_turn["content"] = mask_phi(masked_turn["content"])
        masked.append(masked_turn)
    return masked


# ---------------------------------------------------------------------------
# Log masking wrapper
# ---------------------------------------------------------------------------

class PHIMaskingFilter(logging.Filter):
    """Logging filter that masks PHI in log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_phi(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    mask_phi(a) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: mask_phi(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        return True
