"""
Unified Safety Gate — THE single entry point for ALL LLM output.

Any LLM-produced string that reaches a caller, a file, or a DB row
MUST pass through one of:
    gate_triage_output()   — for triage dispositions / decisions
    gate_outbound_text()   — for any caller/file/DB-facing text

Everything else in this module is private/internal.
No bypass paths. No second gate. No exceptions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from src.safety.red_flag_rules import run_red_flag_rules, RedFlagRuleResult
from src.safety.diagnosis_enforcement import enforce_no_diagnosis, DiagnosisRewriteEvent
from src.safety.triage_output_schema import (
    TriageOutput,
    validate_triage_output,
)
from src.safety.phi_masking import mask_phi

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical Disposition Enum — The ONLY valid set
# ---------------------------------------------------------------------------

CANONICAL_DISPOSITIONS = frozenset(
    {
        "ER_NOW",
        "URGENT",
        "SCHEDULE",
        "SELF_CARE",
        "HUMAN_REVIEW",
    }
)

# Map legacy values to canonical
LEGACY_TO_CANON: Dict[str, str] = {
    # Canonical (identity mappings)
    "ER_NOW": "ER_NOW",
    "URGENT": "URGENT",
    "SCHEDULE": "SCHEDULE",
    "SELF_CARE": "SELF_CARE",
    "HUMAN_REVIEW": "HUMAN_REVIEW",
    # Legacy → Canonical
    "SAFE": "SELF_CARE",
    "PCP": "SCHEDULE",
    "EMERGENCY": "ER_NOW",
    "URGENT_CARE": "URGENT",
    "SAME_DAY": "SCHEDULE",
    "ROUTINE": "SCHEDULE",
    "UNDECIDED": "HUMAN_REVIEW",
}


def normalize_disposition(raw: str) -> str:
    """Map any disposition string to the canonical set.

    Returns HUMAN_REVIEW for unrecognised values (fail-closed).
    """
    upper = raw.strip().upper()
    canon = LEGACY_TO_CANON.get(upper)
    if canon is None:
        logger.warning(f"[GATE] Unknown disposition '{raw}' — mapping to HUMAN_REVIEW")
        return "HUMAN_REVIEW"
    return canon


# ---------------------------------------------------------------------------
# GateContext — required metadata for every gate call
# ---------------------------------------------------------------------------


@dataclass
class GateContext:
    """Context required by both gate functions.

    Build ONE of these per LLM call and pass it through.
    """

    session_id: str = ""
    caller_utterance: str = ""
    chief_complaint: Optional[str] = None
    red_flags_reported: List[str] = field(default_factory=list)
    symptom_severity: Optional[str] = None
    confusion_score: float = 0.0
    protocol_references: List[str] = field(default_factory=list)
    model_version: str = "unknown"
    confidence_min_threshold: float = 0.60
    store_phi: bool = False  # mirrors config.STORE_PHI


# ---------------------------------------------------------------------------
# Safe fallback message — spoken to caller when system cannot proceed
# ---------------------------------------------------------------------------

SAFE_FALLBACK_MESSAGE = (
    "I cannot safely assess this situation. "
    "Please seek immediate medical attention or speak with a nurse."
)


# ---------------------------------------------------------------------------
# FinalDecision — the single output of gate_triage_output
# ---------------------------------------------------------------------------


@dataclass
class FinalDecision:
    """Immutable decision produced by the safety gate.

    This is the ONLY data structure that may drive downstream actions
    (voice response, storage, reporting).
    """

    disposition: str  # canonical: ER_NOW | URGENT | SCHEDULE | SELF_CARE | HUMAN_REVIEW
    urgency_level: str  # CRITICAL | HIGH | MEDIUM | LOW
    confidence_score: float  # 0.0–1.0
    rules_triggered: List[str]  # Red-flag rule IDs
    red_flags_triggered: List[str]  # Red-flag descriptions
    escalation_required: bool
    protocol_references: List[str]  # Protocol IDs used
    model_version: str
    timestamp: str  # ISO-8601
    message_to_caller: str  # Safe, voice-friendly text
    safe_fallback_used: bool = False
    diagnosis_rewrites: List[Dict] = field(default_factory=list)
    gate_trace: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 1: gate_triage_output
# ═══════════════════════════════════════════════════════════════════════════


def gate_triage_output(
    raw_llm: dict,
    ctx: "GateContext | dict",
) -> FinalDecision:
    """Gate a triage decision from LLM JSON output.

    Layers (strict order — each may override/reject):
      1. Red-flag rule engine → forced disposition if triggered
      2. Diagnosis enforcement → rewrite diagnostic claims
      3. Schema validation → reject if invalid JSON
      4. Protocol hierarchy enforcement → RULES > PROTOCOL > LLM
      5. Confidence floor check → escalate if below threshold
      6. Disposition normalisation → canonical enum only

    Args:
        raw_llm: Raw dict parsed from LLM response.
        ctx: GateContext or legacy dict with session metadata.

    Returns:
        FinalDecision — the ONLY acceptable output for downstream use.
    """
    # Accept legacy dict callers (backward compat)
    if isinstance(ctx, dict):
        ctx = GateContext(
            **{k: v for k, v in ctx.items() if k in GateContext.__dataclass_fields__}
        )
    gate_trace: List[str] = []
    now = datetime.now(timezone.utc).isoformat()

    try:
        return _gate_triage_impl(raw_llm, ctx, gate_trace, now)
    except Exception as exc:
        logger.error(f"[GATE] Unhandled exception in gate_triage_output: {exc}")
        gate_trace.append(f"exception:{exc}")
        return _fail_closed(
            reason=f"gate_exception:{exc}",
            model_version=ctx.model_version,
            protocol_references=ctx.protocol_references,
            gate_trace=gate_trace,
            timestamp=now,
        )


# ═══════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 2: gate_outbound_text
# ═══════════════════════════════════════════════════════════════════════════

# Unsafe instruction patterns (compiled once)
_UNSAFE_INSTRUCTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(take|use|apply|give)\s+(yourself\s+)?(\d+\s+)?(mg|milligram|tablet|dose|pill)\b",
        r"\byou\s+(don\s*'?\s*t|do\s+not)\s+need\s+(to\s+)?(see|call|go\s+to|visit|contact)\b",
        r"\bno\s+need\s+(for\s+)?(emergency|er|hospital|911|9\s*1\s*1|doctor)\b",
        r"\byou\s+'?re\s+fine\b",
        r"\bit\s+is\s+not\s+(serious|dangerous|urgent|an\s+emergency)\b",
        r"\bstay\s+(home|at\s+home)\s+and\s+(rest|wait)\b",
        r"\bdon\s*'?\s*t\s+(go\s+to|call|visit)\s+(the\s+)?(er|emergency|hospital|911|9\s*1\s*1)\b",
    ]
]

# PHI-probing patterns — questions that ask for data beyond intake design
_PHI_PROBING_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(social\s+security|ssn|insurance|policy)\s*(number|id|#)\b",
        r"\b(credit\s+card|bank\s+account|routing\s+number)\b",
        r"\byour\s+address\b",
    ]
]

# Email is normal business contact info for a non-clinical vertical (e.g. a
# Birchwood booking confirmation) but PHI in a clinical context. The email-probing
# question is therefore blocked ONLY when the vertical is NOT storing PHI
# (store_phi=False — healthcare). SSN/card/bank/address stay blocked everywhere.
_EMAIL_PROBING_PATTERN = re.compile(r"\byour\s+(email|e-mail)\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Role / credential claim blocker (STOP-SHIP — Part 1)
# ---------------------------------------------------------------------------

_ROLE_CLAIM_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bi\s+am\s+a\s+(nurse|doctor|physician|clinician|paramedic|medical\s+professional|healthcare\s+provider|practitioner)\b",
        r"\bi\s*[\u2019']?\s*m\s+a\s+(nurse|doctor|physician|clinician|paramedic|medical\s+professional|healthcare\s+provider|practitioner)\b",
        r"\bas\s+a\s+(nurse|doctor|physician|clinician|paramedic|medical\s+professional|healthcare\s+provider|practitioner)\b",
        r"\bi\s+am\s+your\s+(nurse|doctor|physician|clinician|paramedic)\b",
        r"\bi\s+can\s+(diagnose|prescribe|treat|provide\s+medical\s+treatment)\b",
    ]
]

_ROLE_CLAIM_DISCLAIMER = (
    "I'm an automated triage assistant. I can ask questions and guide next steps, "
    "and I can help connect you with a nurse if needed."
)

_ROLE_CLAIM_SAFE_FALLBACK = (
    "I'm an automated assistant and I can't provide a safe response right now. "
    "Please speak with a nurse or seek urgent care if needed."
)


def _log_safety_event(
    event_type: str, severity: str = "HIGH", details: dict | None = None
) -> None:
    """Log a structured safety event at the specified severity."""
    payload = {
        "safety_event": event_type,
        "severity": severity,
        **(details or {}),
    }
    if severity == "CRITICAL":
        logger.critical(f"[SAFETY_EVENT] {payload}")
    elif severity == "HIGH":
        logger.error(f"[SAFETY_EVENT] {payload}")
    else:
        logger.warning(f"[SAFETY_EVENT] {payload}")


# Max outbound text length for caller-facing messages
_MAX_QUESTION_LEN = 500
_MAX_HANDOFF_LEN = 5000


def gate_outbound_text(
    text: str,
    ctx: GateContext,
    kind: Literal["question", "phase1_reply", "handoff"],
) -> str:
    """Gate any LLM-produced text before it reaches a caller, file, or DB.

    Checks (applied to ALL kinds):
      1. Diagnosis enforcement — rewrite diagnostic claims
      2. Unsafe instruction removal — flag and remove
      3. PHI masking (when store_phi=False)

    Additional checks for "question" and "phase1_reply":
      4. PHI-probing block — remove questions asking for PHI
      5. Length truncation

    Args:
        text: Raw LLM-produced text.
        ctx: GateContext with session metadata.
        kind: The destination of this text.

    Returns:
        Gated text safe for its destination.
    """
    if not text or not text.strip():
        return text

    gated = text

    # ── 0. Role / credential claim blocker (STOP-SHIP) ────────────────────
    role_matches = [pat for pat in _ROLE_CLAIM_PATTERNS if pat.search(gated)]
    if role_matches:
        _log_safety_event(
            "ROLE_CLAIM_DETECTED",
            severity="HIGH",
            details={
                "kind": kind,
                "session_id": ctx.session_id,
                "claim_count": len(role_matches),
            },
        )
        if len(role_matches) >= 2:
            # Multiple role/credential claims → replace entire output
            return _ROLE_CLAIM_SAFE_FALLBACK
        # Single claim → replace entire output with approved disclaimer
        return _ROLE_CLAIM_DISCLAIMER

    # ── 1. Diagnosis enforcement ──────────────────────────────────────────
    gated, _rewrites = enforce_no_diagnosis(gated)

    # ── 2. Unsafe instruction removal ─────────────────────────────────────
    for pat in _UNSAFE_INSTRUCTION_PATTERNS:
        if pat.search(gated):
            logger.warning(
                f"[GATE] Unsafe instruction detected in {kind} text, removing"
            )
            gated = pat.sub("[safety instruction removed]", gated)

    # ── 3. PHI masking ────────────────────────────────────────────────────
    if not ctx.store_phi:
        gated = mask_phi(gated)

    # ── 4. PHI-probing block (caller-facing text only) ────────────────────
    if kind in ("question", "phase1_reply"):
        probing_patterns = list(_PHI_PROBING_PATTERNS)
        if not ctx.store_phi:
            # Clinical context: also block asking for an email (PHI).
            probing_patterns.append(_EMAIL_PROBING_PATTERN)
        for pat in probing_patterns:
            if pat.search(gated):
                logger.warning(f"[GATE] PHI-probing question blocked in {kind}")
                gated = pat.sub("[question removed for privacy]", gated)

    # ── 5. Length truncation ──────────────────────────────────────────────
    max_len = (
        _MAX_QUESTION_LEN if kind in ("question", "phase1_reply") else _MAX_HANDOFF_LEN
    )
    if len(gated) > max_len:
        gated = gated[:max_len].rsplit(" ", 1)[0] + "..."

    return gated


# ═══════════════════════════════════════════════════════════════════════════
#  PRIVATE implementation
# ═══════════════════════════════════════════════════════════════════════════

_URGENCY_MAP = {
    "ER_NOW": "CRITICAL",
    "URGENT": "HIGH",
    "SCHEDULE": "MEDIUM",
    "SELF_CARE": "LOW",
    "HUMAN_REVIEW": "HIGH",
}


def _disposition_to_urgency(disposition: str) -> str:
    return _URGENCY_MAP.get(disposition.upper(), "HIGH")


def _gate_triage_impl(
    raw_llm: dict,
    ctx: GateContext,
    gate_trace: List[str],
    now: str,
) -> FinalDecision:
    """Internal implementation of gate_triage_output."""

    # ── LAYER 1: RED-FLAG RULE ENGINE ─────────────────────────────────────
    gate_trace.append("layer1:red_flag_rules:start")
    try:
        rf_result: RedFlagRuleResult = run_red_flag_rules(
            utterance=ctx.caller_utterance,
            chief_complaint=ctx.chief_complaint,
            red_flags_reported=ctx.red_flags_reported,
            symptom_severity=ctx.symptom_severity,
            confusion_score=ctx.confusion_score,
        )
    except Exception as exc:
        logger.error(f"[GATE] Red-flag rule engine exception: {exc}")
        gate_trace.append(f"layer1:exception:{exc}")
        return _fail_closed(
            reason=f"red_flag_engine_exception:{exc}",
            model_version=ctx.model_version,
            protocol_references=ctx.protocol_references,
            gate_trace=gate_trace,
            timestamp=now,
        )

    gate_trace.append(
        f"layer1:red_flag_rules:done:disposition={rf_result.forced_disposition},"
        f"score={rf_result.total_score},triggered={rf_result.triggered_rule_ids}"
    )

    # Hard override: if rules force a disposition, LLM output is IGNORED
    if rf_result.forced_disposition is not None:
        logger.warning(
            f"[GATE] Red-flag override: {rf_result.forced_disposition} "
            f"rules={rf_result.triggered_rule_ids}"
        )
        canon_disp = normalize_disposition(rf_result.forced_disposition)
        urgency = "CRITICAL" if canon_disp == "ER_NOW" else "HIGH"
        return FinalDecision(
            disposition=canon_disp,
            urgency_level=urgency,
            confidence_score=0.0,
            rules_triggered=rf_result.triggered_rule_ids,
            red_flags_triggered=rf_result.triggered_descriptions,
            escalation_required=True,
            protocol_references=ctx.protocol_references,
            model_version=ctx.model_version,
            timestamp=now,
            message_to_caller=rf_result.escalation_script or SAFE_FALLBACK_MESSAGE,
            gate_trace=gate_trace,
        )

    # ── LAYER 2: DIAGNOSIS ENFORCEMENT ────────────────────────────────────
    gate_trace.append("layer2:diagnosis_enforcement:start")
    rewrite_events: List[DiagnosisRewriteEvent] = []
    safe_llm_output = dict(raw_llm)  # shallow copy

    for key, value in safe_llm_output.items():
        if isinstance(value, str):
            cleaned, events = enforce_no_diagnosis(value)
            if events:
                safe_llm_output[key] = cleaned
                rewrite_events.extend(events)
                gate_trace.append(f"layer2:rewrite:{key}:{len(events)}_violations")

    gate_trace.append(
        f"layer2:diagnosis_enforcement:done:rewrites={len(rewrite_events)}"
    )

    # ── LAYER 3: SCHEMA VALIDATION (with retry) ──────────────────────────
    gate_trace.append("layer3:schema_validation:start")
    validated: Optional[TriageOutput] = validate_triage_output(safe_llm_output)

    if validated is None:
        logger.error("[GATE] Schema validation failed — using safe fallback")
        gate_trace.append("layer3:schema_validation:FAILED")
        return FinalDecision(
            disposition="HUMAN_REVIEW",
            urgency_level="HIGH",
            confidence_score=0.0,
            rules_triggered=rf_result.triggered_rule_ids,
            red_flags_triggered=rf_result.triggered_descriptions,
            escalation_required=True,
            protocol_references=ctx.protocol_references,
            model_version=ctx.model_version,
            timestamp=now,
            message_to_caller=SAFE_FALLBACK_MESSAGE,
            safe_fallback_used=True,
            diagnosis_rewrites=[e.__dict__ for e in rewrite_events],
            gate_trace=gate_trace,
        )

    gate_trace.append("layer3:schema_validation:passed")

    # ── LAYER 4: PROTOCOL HIERARCHY + DISPOSITION NORMALISATION ──────────
    gate_trace.append("layer4:protocol_hierarchy:start")
    final_disposition = normalize_disposition(validated.disposition)
    final_urgency = _disposition_to_urgency(final_disposition)

    # If weighted flags fired (but didn't force ER_NOW), LLM can't downgrade
    if rf_result.total_score > 0 and rf_result.total_score < 10:
        if final_disposition in ("SELF_CARE", "SCHEDULE"):
            gate_trace.append(
                f"layer4:protocol_hierarchy:upgrade:{final_disposition}->URGENT"
            )
            final_disposition = "URGENT"
            final_urgency = "HIGH"

    gate_trace.append(f"layer4:protocol_hierarchy:done:disposition={final_disposition}")

    # ── LAYER 5: CONFIDENCE FLOOR ENFORCEMENT ────────────────────────────
    gate_trace.append("layer5:confidence_floor:start")
    final_confidence = validated.confidence_score
    escalation_required = validated.escalation_required

    if final_confidence < ctx.confidence_min_threshold:
        gate_trace.append(
            f"layer5:confidence_floor:below:{final_confidence}<{ctx.confidence_min_threshold}"
        )
        escalation_required = True
        if final_disposition not in ("ER_NOW", "URGENT"):
            final_disposition = "HUMAN_REVIEW"
            final_urgency = "HIGH"

    gate_trace.append(f"layer5:confidence_floor:done:escalation={escalation_required}")

    # ── BUILD FINAL DECISION ─────────────────────────────────────────────
    caller_msg = validated.message_to_caller or SAFE_FALLBACK_MESSAGE
    # Gate the caller message too
    caller_msg_gated, msg_rewrites = enforce_no_diagnosis(caller_msg)
    if not ctx.store_phi:
        caller_msg_gated = mask_phi(caller_msg_gated)

    return FinalDecision(
        disposition=final_disposition,
        urgency_level=final_urgency,
        confidence_score=final_confidence,
        rules_triggered=rf_result.triggered_rule_ids + validated.rules_triggered,
        red_flags_triggered=rf_result.triggered_descriptions
        + validated.red_flags_triggered,
        escalation_required=escalation_required,
        protocol_references=ctx.protocol_references + validated.protocol_references,
        model_version=ctx.model_version,
        timestamp=now,
        message_to_caller=caller_msg_gated,
        diagnosis_rewrites=[e.__dict__ for e in rewrite_events + msg_rewrites],
        gate_trace=gate_trace,
    )


def _fail_closed(
    reason: str,
    model_version: str,
    protocol_references: List[str],
    gate_trace: List[str],
    timestamp: str,
) -> FinalDecision:
    """Produce a fail-closed FinalDecision (escalate to human)."""
    gate_trace.append(f"fail_closed:{reason}")
    return FinalDecision(
        disposition="HUMAN_REVIEW",
        urgency_level="CRITICAL",
        confidence_score=0.0,
        rules_triggered=[f"fail_closed:{reason}"],
        red_flags_triggered=[],
        escalation_required=True,
        protocol_references=protocol_references,
        model_version=model_version,
        timestamp=timestamp,
        message_to_caller=SAFE_FALLBACK_MESSAGE,
        safe_fallback_used=True,
        gate_trace=gate_trace,
    )


# ---------------------------------------------------------------------------
# Backward-compat: re-export old name so existing imports don't break
# ---------------------------------------------------------------------------
# Tests and code that import from src.safety.safety_gate will be updated
# to import from src.safety.gate instead.
