"""PII redaction for enrichment payloads.

Before any call text leaves the system, direct identifiers are replaced
with stable tokens ([CUSTOMER_NAME], [PHONE_1], ...). Tokenization (not
deletion) keeps the text coherent for the LLM; results re-associate with
the record locally by session id, and drafts that address the customer use
the tokens so the admin view can substitute real values locally.

Two passes:
1. Exact-value pass — the structured record already KNOWS the caller's
   name, phone, email, address, plate, and claim number; every occurrence
   (case-insensitive, punctuation-tolerant for phones) is tokenized.
2. Pattern sweep — phone-number/email shapes that survived pass 1
   (e.g. a second number spoken mid-story) are caught by regex.

Locations and vehicle details are intentionally NOT redacted in redact
mode: they are the working material of the enrichment features and are not
direct identifiers. The data-flow doc (docs/ENRICHMENT_DATA_FLOW.md)
records this decision for privacy review.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

_PHONE_PATTERN = re.compile(
    r"(?<!\w)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)"
)
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")


class RedactionResult(BaseModel):
    text: str
    tokens: dict[str, str] = Field(default_factory=dict)  # token -> real value
    mode: str = "redact"


def _phone_variants(phone: str) -> list[str]:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 7:
        return []
    variants = {phone}
    national = digits[-10:] if len(digits) >= 10 else digits
    if len(national) == 10:
        variants.add(national)
        variants.add(f"{national[:3]}-{national[3:6]}-{national[6:]}")
        variants.add(f"({national[:3]}) {national[3:6]}-{national[6:]}")
        variants.add(f"{national[:3]} {national[3:6]} {national[6:]}")
        variants.add(f"+1{national}")
        variants.add(f"+1 {national[:3]} {national[3:6]} {national[6:]}")
    return [v for v in variants if v]


def redact_for_provider(
    text: str,
    record: dict[str, Any],
    *,
    mode: str = "redact",
) -> RedactionResult:
    """Tokenize direct identifiers in *text* using the structured record."""
    if mode == "raw":
        return RedactionResult(text=text or "", tokens={}, mode="raw")

    tokens: dict[str, str] = {}
    out = text or ""

    def tokenize(value: str | None, token: str) -> None:
        nonlocal out
        if not value or not str(value).strip():
            return
        value = str(value).strip()
        tokens[token] = value
        out = re.sub(re.escape(value), token, out, flags=re.IGNORECASE)

    # Pass 1 — exact values from the structured record.
    tokenize(record.get("caller_name"), "[CUSTOMER_NAME]")
    name = str(record.get("caller_name") or "").strip()
    if name and " " in name:
        # First/last name alone also identify the caller.
        for i, part in enumerate(name.split()):
            if len(part) > 2:
                tokens.setdefault("[CUSTOMER_NAME]", name)
                out = re.sub(
                    rf"\b{re.escape(part)}\b", "[CUSTOMER_NAME]", out, flags=re.I
                )
    for variant in _phone_variants(str(record.get("phone") or "")):
        out = re.sub(re.escape(variant), "[PHONE_1]", out, flags=re.IGNORECASE)
    if record.get("phone"):
        tokens["[PHONE_1]"] = str(record["phone"])
    tokenize(record.get("email"), "[EMAIL_1]")
    tokenize(record.get("address"), "[ADDRESS]")
    tokenize(record.get("license_plate"), "[PLATE]")
    tokenize(record.get("claim_number"), "[CLAIM_REF]")

    # Pass 2 — pattern sweep for identifiers pass 1 didn't know about.
    extra_phones = _PHONE_PATTERN.findall(out)
    for i, found in enumerate(dict.fromkeys(extra_phones), start=2):
        token = f"[PHONE_{i}]"
        tokens[token] = found
        out = out.replace(found, token)
    extra_emails = _EMAIL_PATTERN.findall(out)
    for i, found in enumerate(dict.fromkeys(extra_emails), start=2):
        token = f"[EMAIL_{i}]"
        tokens[token] = found
        out = out.replace(found, token)

    return RedactionResult(text=out, tokens=tokens, mode="redact")


def restore_tokens(text: str, tokens: dict[str, str]) -> str:
    """Locally substitute tokens back (admin display / draft rendering)."""
    out = text or ""
    for token, value in tokens.items():
        out = out.replace(token, value)
    return out
