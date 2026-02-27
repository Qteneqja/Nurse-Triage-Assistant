"""
Canonical Disposition Enum — THE single source of truth.

Every triage disposition in the system MUST be one of these values.
No legacy values (SAFE, PCP, EMERGENCY, ROUTINE, URGENT_CARE, SAME_DAY)
may exit the API layer or be stored in the database.

Import this module — not gate.py — when you need the enum in type hints.
"""
from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class CanonicalDisposition(str, Enum):
    """The ONE canonical disposition enum for the entire system."""
    ER_NOW = "ER_NOW"
    URGENT = "URGENT"
    SCHEDULE = "SCHEDULE"
    SELF_CARE = "SELF_CARE"
    HUMAN_REVIEW = "HUMAN_REVIEW"


# Frozen set for fast membership checks
CANONICAL_DISPOSITION_VALUES: FrozenSet[str] = frozenset(
    d.value for d in CanonicalDisposition
)


def assert_canonical(disposition: str) -> str:
    """Assert that a disposition string is canonical. Raises ValueError if not."""
    if disposition not in CANONICAL_DISPOSITION_VALUES:
        raise ValueError(
            f"Non-canonical disposition '{disposition}'. "
            f"Must be one of: {sorted(CANONICAL_DISPOSITION_VALUES)}"
        )
    return disposition
