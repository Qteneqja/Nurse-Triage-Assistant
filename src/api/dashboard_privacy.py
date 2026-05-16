"""Privacy helpers for dashboard/admin read models."""

from __future__ import annotations

import re
from typing import Any

from src.safety.phi_masking import mask_phi, mask_transcript

_PHONE_KEYS = {
    "phone",
    "phone_number",
    "caller_phone",
    "caller_phone_number",
    "called_phone",
    "called_phone_number",
    "callback_phone",
    "default_twilio_phone_number",
    "twilio_phone_number",
}
_NAME_KEYS = {
    "name",
    "caller_name",
    "patient_name",
    "full_name",
    "tenant_name",
}
_DOB_KEYS = {"dob", "date_of_birth", "birth_date"}


def mask_phone(value: Any) -> Any:
    """Mask a phone-like value, preserving only the last four digits."""
    if value is None:
        return None
    text = str(value)
    digits = re.sub(r"\D", "", text)
    if len(digits) < 4:
        return "***"
    return f"***-***-{digits[-4:]}"


def mask_name(value: Any) -> Any:
    """Mask a person name to initials."""
    if value is None:
        return None
    parts = [part for part in str(value).strip().split() if part]
    if not parts:
        return ""
    return " ".join(f"{part[0].upper()}." for part in parts)


def safe_display_value(value: Any, field_name: str | None = None) -> Any:
    """Return a dashboard-safe representation for a single value."""
    if value is None:
        return None

    key = (field_name or "").lower()
    if key in _PHONE_KEYS or key.endswith("_phone"):
        return mask_phone(value)
    if key in _NAME_KEYS or key.endswith("_name"):
        return mask_name(value)
    if key in _DOB_KEYS or "date_of_birth" in key:
        return "[REDACTED_DOB]"
    if key in {"caller_id"}:
        return _mask_identifier(value)
    if isinstance(value, str):
        return mask_phi(value)
    return value


def mask_dashboard_payload(value: Any, field_name: str | None = None) -> Any:
    """Recursively mask dashboard payloads before returning them to clients."""
    if isinstance(value, dict):
        return {
            key: mask_dashboard_payload(item, str(key))
            for key, item in value.items()
            if key not in {"raw_model_output"}
        }
    if isinstance(value, list):
        if value and all(isinstance(item, dict) and "text" in item for item in value):
            return mask_transcript(value)
        return [mask_dashboard_payload(item, field_name) for item in value]
    return safe_display_value(value, field_name)


def _mask_identifier(value: Any) -> str:
    text = str(value)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 7:
        return mask_phone(text)
    if len(text) <= 8:
        return "***"
    return f"{text[:2]}...{text[-4:]}"
