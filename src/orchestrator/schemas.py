"""
Orchestrator Pydantic Schemas

Defines all structured data models for the multi-agent orchestration flow.
Every LLM output is validated against these schemas.
"""

from __future__ import annotations

import re as _re
from datetime import datetime, UTC
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DispositionCategory(str, Enum):
    """Canonical triage disposition — the ONE enum for the entire system."""

    ER_NOW = "ER_NOW"
    URGENT = "URGENT"
    SCHEDULE = "SCHEDULE"
    SELF_CARE = "SELF_CARE"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class SafetyLevel(str, Enum):
    """Severity level for safety flags."""

    EMERGENT = "EMERGENT"
    URGENT = "URGENT"
    ADVISORY = "ADVISORY"


# ---------------------------------------------------------------------------
# Phase 1 enums
# ---------------------------------------------------------------------------


class Phase1Disposition(str, Enum):
    """Phase 1 disposition values — canonical set for LLM output."""

    ER_NOW = "ER_NOW"
    URGENT = "URGENT"
    SCHEDULE = "SCHEDULE"
    SELF_CARE = "SELF_CARE"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class Phase1NextAction(str, Enum):
    """Phase 1 next action values."""

    ASK_QUESTION = "ASK_QUESTION"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    END_CALL = "END_CALL"


# ---------------------------------------------------------------------------
# Structured Intake State (accumulated across turns)
# ---------------------------------------------------------------------------
# Shared coercion helpers for LLM-produced values
# ---------------------------------------------------------------------------


def _coerce_sex(v: object) -> object:
    """Normalize caller_sex variants (e.g. 'Male', 'M', 'F') to lowercase literals.

    Also handles common STT homophones: ``'mail'`` → ``'male'``,
    ``'female.'`` → ``'female'`` (trailing punctuation stripped first).
    """
    if isinstance(v, str):
        normed = v.strip().lower().rstrip(".?,!")  # strip STT-added punctuation
        return {
            "m": "male",
            "male": "male",
            "mail": "male",
            "f": "female",
            "female": "female",
        }.get(normed, normed)
    return v


def _coerce_severity(v: object) -> object:
    """Map numeric 1-10 strings to mild/moderate/severe categories."""
    if isinstance(v, str):
        normed = v.strip().lower()
        if normed in ("mild", "moderate", "severe", "unknown"):
            return normed
        try:
            n = int(normed)
            if n <= 3:
                return "mild"
            elif n <= 6:
                return "moderate"
            else:
                return "severe"
        except ValueError:
            pass
    return v


# ---------------------------------------------------------------------------


class StructuredIntakeState(BaseModel):
    """Tracked fields accumulated from caller utterances across all turns."""

    caller_name: Optional[str] = None
    caller_age: Optional[int] = Field(default=None, ge=0, le=120)
    caller_sex: Optional[Literal["male", "female", "unknown"]] = None
    chief_complaint: Optional[str] = None
    onset_time: Optional[str] = None
    symptom_severity: Optional[Literal["mild", "moderate", "severe", "unknown"]] = None

    @field_validator("caller_sex", mode="before")
    @classmethod
    def _normalize_sex(cls, v: object) -> object:
        return _coerce_sex(v)

    @field_validator("symptom_severity", mode="before")
    @classmethod
    def _normalize_severity(cls, v: object) -> object:
        return _coerce_severity(v)

    red_flags_reported: List[str] = Field(default_factory=list)
    relevant_history: List[str] = Field(default_factory=list)
    meds: List[str] = Field(default_factory=list)
    allergies: List[str] = Field(default_factory=list)
    pregnancy_status: Optional[
        Literal["pregnant", "not_pregnant", "unknown", "not_applicable"]
    ] = None
    vitals_if_known: Optional[str] = None
    confusion_or_poor_historian_score: float = Field(default=0.0, ge=0.0, le=1.0)
    language_or_comms_barriers: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    notes: Optional[str] = None

    def filled_field_count(self) -> int:
        """Count how many optional fields have been filled."""
        count = 0
        for name in [
            "caller_age",
            "caller_sex",
            "chief_complaint",
            "onset_time",
            "symptom_severity",
            "pregnancy_status",
            "vitals_if_known",
            "location",
        ]:
            if getattr(self, name) is not None:
                count += 1
        for name in ["red_flags_reported", "relevant_history", "meds", "allergies"]:
            if getattr(self, name):
                count += 1
        return count


# ---------------------------------------------------------------------------
# Typed Patch Model (for LLM-extracted field updates per turn)
# ---------------------------------------------------------------------------


class IntakeStatePatch(BaseModel):
    """Partial update to StructuredIntakeState fields from a single turn.

    All fields are optional — only fields extracted in *this* turn should be set.
    """

    caller_name: Optional[str] = None
    caller_age: Optional[int] = Field(default=None, ge=0, le=120)
    caller_sex: Optional[Literal["male", "female", "unknown"]] = None
    chief_complaint: Optional[str] = None
    onset_time: Optional[str] = None
    symptom_severity: Optional[Literal["mild", "moderate", "severe", "unknown"]] = None

    @field_validator("caller_sex", mode="before")
    @classmethod
    def _normalize_sex(cls, v: object) -> object:
        return _coerce_sex(v)

    @field_validator("symptom_severity", mode="before")
    @classmethod
    def _normalize_severity(cls, v: object) -> object:
        return _coerce_severity(v)

    red_flags_reported: Optional[List[str]] = None
    relevant_history: Optional[List[str]] = None
    meds: Optional[List[str]] = None
    allergies: Optional[List[str]] = None
    pregnancy_status: Optional[
        Literal["pregnant", "not_pregnant", "unknown", "not_applicable"]
    ] = None
    vitals_if_known: Optional[str] = None
    confusion_or_poor_historian_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0
    )
    language_or_comms_barriers: Optional[List[str]] = None
    location: Optional[str] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Safety Flag (shared between deterministic + LLM)
# ---------------------------------------------------------------------------


class SafetyFlag(BaseModel):
    """A single safety concern detected by rules or LLM."""

    source: Literal["deterministic", "llm"] = Field(
        description="Origin of the safety flag",
    )
    level: SafetyLevel
    flag: str = Field(description="Short description of the safety concern")
    reason_for_audit: str = Field(
        description="Why this flag was raised, for audit trail"
    )
    script_to_say: Optional[str] = Field(
        default=None,
        description="Voice-friendly script to say to the caller if this flag triggers escalation",
    )


# ---------------------------------------------------------------------------
# Conversation Turn
# ---------------------------------------------------------------------------


class ConversationTurn(BaseModel):
    """A single turn in the caller–assistant conversation."""

    role: Literal["caller", "assistant"]
    text: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_llm_message(self) -> dict:
        """Convert to LLM-compatible message dict."""
        return {
            "role": "user" if self.role == "caller" else "assistant",
            "content": self.text,
        }


# ---------------------------------------------------------------------------
# LLM Output: Intake Turn (Mode 1 — one call per user turn)
# ---------------------------------------------------------------------------


class IntakeTurnOutput(BaseModel):
    """Schema for the LLM response during each questioning turn.

    The LLM MUST return valid JSON matching this schema.
    """

    extracted_fields_update: IntakeStatePatch = Field(
        default_factory=IntakeStatePatch,
        description="Partial update to StructuredIntakeState fields from the caller's latest answer",
    )
    missing_fields_prioritized: List[str] = Field(
        default_factory=list,
        description="List of StructuredIntakeState field names still missing, in priority order",
    )
    next_question: str = Field(
        max_length=180,
        description="Short, voice-friendly question to ask the caller next",
    )
    llm_safety_flags: List[SafetyFlag] = Field(
        default_factory=list,
        description="Safety concerns detected by the LLM in this turn",
    )
    safety_flags_coerced: bool = Field(
        default=False,
        exclude=True,
        description="Internal: True if llm_safety_flags were coerced from strings",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="LLM's confidence (0-1) that enough information has been gathered to finalize",
    )
    expected_answer_type: Literal["yes_no", "number", "free_text", "choice"] = Field(
        default="free_text",
        description="Expected answer type for the next question",
    )
    finalize_ready: bool = Field(
        default=False,
        description="Whether the LLM believes enough info has been gathered to finalize",
    )

    @field_validator("llm_safety_flags", mode="before")
    @classmethod
    def _coerce_safety_flags(cls, v: object) -> object:
        """Accept plain strings from the LLM and coerce to SafetyFlag dicts."""
        if not isinstance(v, list):
            return v
        out: list = []
        for item in v:
            if isinstance(item, str):
                out.append(
                    {
                        "source": "llm",
                        "level": SafetyLevel.ADVISORY.value,
                        "flag": item,
                        "reason_for_audit": f"LLM-detected: {item}",
                    }
                )
            else:
                out.append(item)
        return out

    @model_validator(mode="before")
    @classmethod
    def _detect_safety_flag_coercion(cls, data: object) -> object:
        """Set safety_flags_coerced=True if raw llm_safety_flags contains strings."""
        if isinstance(data, dict):
            flags = data.get("llm_safety_flags", [])
            if isinstance(flags, list) and any(isinstance(x, str) for x in flags):
                data["safety_flags_coerced"] = True
        return data


# ---------------------------------------------------------------------------
# Structured SBAR
# ---------------------------------------------------------------------------


class SBAR(BaseModel):
    """Structured SBAR clinical handoff format."""

    situation: str = ""
    background: str = ""
    assessment: str = ""
    recommendation: str = ""

    def to_text(self) -> str:
        """Render as a plain-text SBAR report."""
        return (
            f"S: {self.situation}\n"
            f"B: {self.background}\n"
            f"A: {self.assessment}\n"
            f"R: {self.recommendation}"
        )


def _parse_sbar_text(text: str) -> SBAR:
    """Best-effort parse of SBAR text into structured SBAR fields."""
    sections: dict[str, str] = {
        "situation": "",
        "background": "",
        "assessment": "",
        "recommendation": "",
    }
    label_map = {
        "S": "situation",
        "B": "background",
        "A": "assessment",
        "R": "recommendation",
    }
    # Match S:/B:/A:/R: section headers
    pattern = r"(?:^|\n)\s*([SBAR])\s*[:\-]\s*(.*?)(?=(?:\n\s*[SBAR]\s*[:\-])|$)"
    for m in _re.finditer(pattern, text, _re.DOTALL | _re.IGNORECASE):
        key = label_map.get(m.group(1).upper())
        if key:
            sections[key] = m.group(2).strip()
    # Fallback: if nothing parsed, put the full text in situation
    if not any(sections.values()):
        sections["situation"] = text.strip()
    return SBAR(**sections)


# ---------------------------------------------------------------------------
# LLM Output: Finalize (Mode 2 — clinical reasoning + SBAR)
# ---------------------------------------------------------------------------


class FinalizeOutput(BaseModel):
    """Schema for the finalization LLM response.

    Contains disposition, SBAR, safety-net instructions, and patient summary.
    Extra keys from the LLM (e.g. ai_thoughts, _debug_elapsed_ms) are silently
    ignored so that upstream callers never crash on unexpected fields.
    """

    model_config = ConfigDict(extra="ignore")

    disposition: DispositionCategory = Field(
        description="Triage disposition category",
    )
    disposition_reasoning: str = Field(
        default="No clinical reasoning available",
        max_length=400,
        description="Brief reasoning for the chosen disposition",
    )
    safety_net_instructions: List[str] = Field(
        default_factory=list,
        description="Voice-friendly bullet instructions for the caller on when to seek further care",
    )
    sbar_report: Optional[str] = Field(
        default=None,
        description="SBAR-formatted clinical handoff report (backward-compatible text)",
    )
    sbar: Optional[SBAR] = Field(
        default=None,
        description="Structured SBAR; preferred over sbar_report when available",
    )
    patient_summary: str = Field(
        default="Patient summary pending review.",
        max_length=600,
        description="Patient-friendly summary to speak over the phone (2-4 sentences)",
    )
    llm_safety_flags: List[SafetyFlag] = Field(
        default_factory=list,
        description="Any additional safety concerns detected during finalization",
    )
    safety_flags_coerced: bool = Field(
        default=False,
        exclude=True,
        description="Internal: True if llm_safety_flags were coerced from strings",
    )

    # -- Null coercion for nullable text fields --
    @field_validator("disposition_reasoning", mode="before")
    @classmethod
    def _coerce_disposition_reasoning(cls, v: object) -> object:
        """Coerce None → safe default so the field is always a non-null str."""
        if v is None:
            return "Structured validation failure — fallback"
        return v

    @field_validator("patient_summary", mode="before")
    @classmethod
    def _coerce_patient_summary(cls, v: object) -> object:
        """Coerce None → safe default."""
        if v is None:
            return "Patient summary pending review."
        return v

    # -- Tweak #2: coerce safety_net_instructions --
    @field_validator("safety_net_instructions", mode="before")
    @classmethod
    def _coerce_safety_net(cls, v: object) -> list:
        """Accept a single string and split into List[str]."""
        if v is None:
            return []
        if isinstance(v, str):
            lines = [ln.strip() for ln in v.splitlines() if ln.strip()]
            return lines if lines else [v] if v.strip() else []
        if isinstance(v, list):
            return v
        return [str(v)]

    @field_validator("llm_safety_flags", mode="before")
    @classmethod
    def _coerce_safety_flags(cls, v: object) -> object:
        """Accept plain strings from the LLM and coerce to SafetyFlag dicts."""
        if not isinstance(v, list):
            return v
        out: list = []
        for item in v:
            if isinstance(item, str):
                out.append(
                    {
                        "source": "llm",
                        "level": SafetyLevel.ADVISORY.value,
                        "flag": item,
                        "reason_for_audit": f"LLM-detected: {item}",
                    }
                )
            else:
                out.append(item)
        return out

    @model_validator(mode="before")
    @classmethod
    def _detect_safety_flag_coercion(cls, data: object) -> object:
        """Set safety_flags_coerced=True if raw llm_safety_flags contains strings."""
        if isinstance(data, dict):
            flags = data.get("llm_safety_flags", [])
            if isinstance(flags, list) and any(isinstance(x, str) for x in flags):
                data["safety_flags_coerced"] = True
        return data

    # -- Tweak #1: two-way SBAR sync --
    @model_validator(mode="after")
    def _sync_sbar_fields(self):
        """Ensure both sbar and sbar_report are populated (two-way sync).

        - If sbar is present and sbar_report missing → generate text.
        - If sbar_report is present and sbar missing → parse text.
        - If both missing → raise.
        """
        if self.sbar is not None and not self.sbar_report:
            self.sbar_report = self.sbar.to_text()
        elif self.sbar_report and self.sbar is None:
            self.sbar = _parse_sbar_text(self.sbar_report)
        elif self.sbar is None and not self.sbar_report:
            # Fallback: generate a minimal SBAR template instead of raising
            self.sbar_report = (
                "S: Patient completed phone triage. Details unavailable.\n"
                "B: See conversation transcript.\n"
                "A: Manual review required.\n"
                "R: Human clinician review recommended."
            )
            self.sbar = _parse_sbar_text(self.sbar_report)
        return self


# ---------------------------------------------------------------------------
# Deterministic Rule Result
# ---------------------------------------------------------------------------


class RedFlagResult(BaseModel):
    """Result from the deterministic red-flag engine."""

    triggered: bool = False
    level: Optional[SafetyLevel] = None
    script_to_say: Optional[str] = None
    reason_for_audit: Optional[str] = None
    matched_rules: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit Trace (per call)
# ---------------------------------------------------------------------------


class AuditTraceEntry(BaseModel):
    """A single step in the audit trail."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    step: str
    agent: str
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None
    duration_ms: Optional[float] = None
    correlation_id: Optional[str] = None


class AuditTrace(BaseModel):
    """Full audit trail for a call session."""

    session_id: str
    call_sid: Optional[str] = None
    entries: List[AuditTraceEntry] = Field(default_factory=list)
    final_disposition: Optional[DispositionCategory] = None
    deterministic_rules_triggered: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def add_entry(
        self,
        step: str,
        agent: str,
        input_summary: Optional[str] = None,
        output_summary: Optional[str] = None,
        duration_ms: Optional[float] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Append a new audit entry."""
        self.entries.append(
            AuditTraceEntry(
                step=step,
                agent=agent,
                input_summary=input_summary,
                output_summary=output_summary,
                duration_ms=duration_ms,
                correlation_id=correlation_id,
            )
        )


# ---------------------------------------------------------------------------
# Orchestrator Session (wraps everything for one call)
# ---------------------------------------------------------------------------


class OrchestratorSession(BaseModel):
    """Orchestrator-level session state for one call."""

    session_id: str
    call_sid: Optional[str] = None
    turn_count: int = 0
    max_turns: int = 12
    confidence_threshold: float = 0.75
    intake_state: StructuredIntakeState = Field(default_factory=StructuredIntakeState)
    conversation: List[ConversationTurn] = Field(default_factory=list)
    safety_flags: List[SafetyFlag] = Field(default_factory=list)
    audit_trace: Optional[AuditTrace] = None
    is_finalized: bool = False
    finalize_output: Optional[FinalizeOutput] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    llm_coercions: List[str] = Field(default_factory=list)
    # Channel-specific metadata (e.g. Twilio stage tracking)
    channel_metadata: dict = Field(default_factory=dict)

    # ── Phase 1 additions ──────────────────────────────────────────────
    # Confused-caller retry counter (reset each turn; persists across retries)
    unclear_answer_retries: int = Field(default=0, ge=0)
    # Per-turn decision trace (auditable clinical decision log)
    decision_trace: List["DecisionTraceEntry"] = Field(default_factory=list)
    # Expected answer type for the last question asked (Part 3)
    last_expected_answer_type: Optional[
        Literal["yes_no", "number", "free_text", "choice"]
    ] = Field(default=None)
    # ── Phase 5: Intake-gate / transfer-control ────────────────────────
    # How many times the caller has requested nurse transfer without red flags
    nurse_request_resistance_count: int = Field(default=0, ge=0)
    # True if a transfer was allowed before intake was fully complete
    premature_transfer_triggered: bool = Field(default=False)
    # ── Loop-breaker: track repeated SBAR follow-ups ───────────────────
    last_followup_slot: Optional[str] = Field(
        default=None,
        description="The SBAR slot name that was last asked about in a low-confidence follow-up",
    )
    followup_repeat_count: int = Field(
        default=0,
        ge=0,
        description="How many consecutive times the same slot follow-up has been asked",
    )

    def model_post_init(self, __context: object) -> None:
        """Ensure audit_trace exists with correct IDs after construction."""
        if self.audit_trace is None:
            self.audit_trace = AuditTrace(
                session_id=self.session_id,
                call_sid=self.call_sid,
            )
        else:
            self.audit_trace.session_id = self.session_id
            self.audit_trace.call_sid = self.call_sid

    def add_coercion(self, tag: str) -> None:
        """Record a coercion event (idempotent per unique tag)."""
        if tag not in self.llm_coercions:
            self.llm_coercions.append(tag)


# ---------------------------------------------------------------------------
# Phase 1 — Strict LLM Output Schema
# ---------------------------------------------------------------------------


class Phase1TurnOutput(BaseModel):
    """Strict Phase 1 LLM output schema.

    The LLM MUST return ONLY a JSON object matching this schema.
    Validated twice (attempt + repair). On second failure → FAIL CLOSED.
    """

    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="0.0–1.0 confidence that current data is sufficient",
    )
    escalation_required: bool = Field(
        description="True if immediate human/ER escalation is required",
    )
    red_flags_triggered: List[str] = Field(
        default_factory=list,
        description="List of red flag IDs or descriptions detected this turn",
    )
    rules_triggered: List[str] = Field(
        default_factory=list,
        description="Clinical rules that fired this turn",
    )
    next_action: Phase1NextAction = Field(
        description="What the system should do next",
    )
    disposition: Phase1Disposition = Field(
        description="Current triage disposition assessment",
    )

    @field_validator("next_action", mode="before")
    @classmethod
    def _normalize_next_action(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().upper()
        return v

    @field_validator("disposition", mode="before")
    @classmethod
    def _normalize_disposition(cls, v: object) -> object:
        if isinstance(v, str):
            normed = v.strip().upper()
            # Map legacy values to canonical
            _map = {
                "ER_NOW": "ER_NOW",
                "EMERGENCY": "ER_NOW",
                "URGENT": "URGENT",
                "URGENT_CARE": "URGENT",
                "SCHEDULE": "SCHEDULE",
                "SAME_DAY": "SCHEDULE",
                "ROUTINE": "SCHEDULE",
                "PCP": "SCHEDULE",
                "SELF_CARE": "SELF_CARE",
                "SAFE": "SELF_CARE",
                "HUMAN_REVIEW": "HUMAN_REVIEW",
                "UNDECIDED": "HUMAN_REVIEW",
            }
            return _map.get(normed, normed)
        return v


# ---------------------------------------------------------------------------
# Phase 1 — Confidence Deduction Record
# ---------------------------------------------------------------------------


class ConfidenceDeduction(BaseModel):
    """Single deduction applied to the confidence score."""

    reason: str
    amount: float


class ConfidenceBreakdown(BaseModel):
    """Full confidence computation for one turn."""

    raw_score: float = 1.0
    deductions: List[ConfidenceDeduction] = Field(default_factory=list)
    final_score: float = 1.0

    def apply(self, reason: str, amount: float) -> None:
        """Apply a deduction in-place."""
        self.deductions.append(ConfidenceDeduction(reason=reason, amount=amount))
        self.raw_score = max(0.0, self.raw_score - amount)

    def clamp_and_finalise(self) -> float:
        self.final_score = max(0.0, min(1.0, self.raw_score))
        return self.final_score


# ---------------------------------------------------------------------------
# Phase 2 — Protocol Hit (for decision trace)
# ---------------------------------------------------------------------------


class ProtocolHit(BaseModel):
    """A protocol matched during retrieval for a given turn."""

    id: str
    title: str
    version: str


# ---------------------------------------------------------------------------
# Phase 1 — Decision Trace Entry (per-turn audit log)
# ---------------------------------------------------------------------------


class DecisionTraceEntry(BaseModel):
    """One entry in the Phase 1 per-turn decision audit log."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    turn_number: int
    user_text: str
    extracted_entities: dict = Field(default_factory=dict)
    red_flags_triggered: List[str] = Field(default_factory=list)
    rules_triggered: List[str] = Field(default_factory=list)
    confidence_score: float
    disposition: str
    escalation_required: bool
    system_response: str
    confidence_breakdown: Optional[ConfidenceBreakdown] = None
    # Phase 2: protocol retrieval trace
    protocol_hits: List[ProtocolHit] = Field(default_factory=list)
    protocol_citations: List[str] = Field(default_factory=list)
    # Phase 5: intake gate / transfer control fields
    intake_complete: bool = Field(
        default=False,
        description="True if all SBAR-required intake fields were collected before this turn",
    )
    premature_transfer: bool = Field(
        default=False,
        description="True if transfer was allowed before intake was complete",
    )
    resistance_count: int = Field(
        default=0,
        ge=0,
        description="Number of times the caller requested early transfer this session",
    )
    transfer_reason: Optional[str] = Field(
        default=None,
        description="Clinical or policy reason that authorized the transfer",
    )
    override_applied: bool = Field(
        default=False,
        description="True if the transfer-control gate blocked and overrode an LLM transfer attempt",
    )
    # Low-confidence continue-intake fields (Bug 2 fix)
    low_confidence_reprompt: bool = Field(
        default=False,
        description="True when low confidence triggered an intake follow-up question instead of escalation",
    )
    override_reason: Optional[str] = Field(
        default=None,
        description="Named policy override applied this turn, e.g. LOW_CONFIDENCE_CONTINUE_INTAKE",
    )


# Resolve forward reference
OrchestratorSession.model_rebuild()
