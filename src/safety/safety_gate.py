"""
safety_gate.py — DEPRECATED. Thin re-export wrapper.

All real logic lives in src.safety.gate (the ONE gate module).
This file exists solely for backward compatibility with existing
imports. New code MUST import from src.safety.gate directly.
"""

from src.safety.gate import (  # noqa: F401  — re-export
    gate_triage_output as safety_gate,
    gate_outbound_text,
    GateContext,
    FinalDecision,
    SAFE_FALLBACK_MESSAGE,
    CANONICAL_DISPOSITIONS,
    LEGACY_TO_CANON,
    normalize_disposition,
)
