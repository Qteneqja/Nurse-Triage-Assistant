"""
Multi-Agent Orchestrator

Manages the triage flow through logical agent stages:
- Mode 1 (Questioning): deterministic safety check → single LLM call per turn
- Mode 2 (Finalize): deterministic safety check → 1-2 concurrent LLM calls

Design principles:
- One DeepSeek call per user turn during questioning (latency constraint)
- Deterministic safety rules always run first and always override
- All LLM outputs validated against Pydantic schemas
- Conservative defaults on any failure

Phase 1 additions (Clinical Core):
- Pre-check safety gate: score_red_flags() before every LLM call
  CRITICAL flag  → ER_NOW  (no LLM call)
  score ≥ 10     → URGENT  (no LLM call)
- Post-check safety gate: scans LLM text for diagnoses / unsafe instructions
- Deterministic confidence scoring with deductions
- Confused-caller protocol: two-attempt clarification then escalate
- Phase1TurnOutput: strict JSON schema with 2-attempt repair loop
- Decision trace logging: per-turn clinical audit log stored in session
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Optional

from src.llm.client import LLMCallError
from pydantic import ValidationError as _PydanticValidationError
from src.llm.guarded_client import GuardedLLM, get_guarded_llm
from src.orchestrator.intake_gate import TransferControlGate
from src.orchestrator.prompts import (
    INTAKE_TURN_SYSTEM_PROMPT,
    FINALIZE_SYSTEM_PROMPT,
    PHASE1_TURN_SYSTEM_PROMPT,
)
from src.orchestrator.schemas import (
    ConversationTurn,
    ConfidenceBreakdown,
    DecisionTraceEntry,
    DispositionCategory,
    FinalizeOutput,
    IntakeTurnOutput,
    OrchestratorSession,
    Phase1TurnOutput,
    ProtocolHit,
    RedFlagResult,
    SafetyFlag,
    SafetyLevel,
    StructuredIntakeState,
)
from src.orchestrator.validators import (
    safe_finalize_default,
    safe_intake_turn_default,
    safe_phase1_escalation,
    post_check_safety_gate,
    PostCheckViolation,
)
from src.safety.red_flags import check_all as check_red_flags, score_red_flags
from src.safety.gate import (
    gate_triage_output,
    GateContext,
    FinalDecision,
)
from src.observability.sentry_integration import add_safety_gate_breadcrumb
from src.protocols.retriever import ProtocolRetriever, ProtocolSnippet, get_retriever
from src.observability.metrics import get_metrics
from src.observability.logging import set_log_context, clear_log_context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX_TURNS = 12
DEFAULT_CONFIDENCE_THRESHOLD = 0.75
FALLBACK_QUESTIONS = [
    "Can you describe your main symptom or concern?",
    "How long have you been experiencing this?",
    "On a scale of 0 to 10, how severe is this symptom?",
    "Have you noticed anything that makes it better or worse?",
    "Are you taking any medications?",
    "Do you have any drug allergies?",
    "Is there anything else important I should know?",
]

# ---------------------------------------------------------------------------
# Phase 1 Constants
# ---------------------------------------------------------------------------

# Confidence threshold below which escalation is mandatory
PHASE1_CONFIDENCE_ESCALATION_THRESHOLD = 0.60

# ---------------------------------------------------------------------------
# Part 2: Human / Nurse Request Short-Circuit (deterministic, no LLM)
# ---------------------------------------------------------------------------

_HUMAN_REQUEST_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\b(?:talk|speak|connect)\s+(?:to|with)\s+a?\s*(?:nurse|human|agent|representative|person|someone)\b",
        r"\b(?:real|actual|live)\s+(?:person|human|nurse|agent)\b",
        r"\btransfer\s+(?:me|call)?\s*(?:to)?\b",
        r"\bstop\s+the\s+ai\b",
        r"\b(?:want|need|get)\s+a?\s*(?:nurse|human|agent|representative|real\s+person)\b",
        r"\bhuman\s+(?:please|now|help)\b",
        r"^\s*(?:nurse|human|agent|representative)\s*[.!?]?\s*$",
    ]
]

_HUMAN_REQUEST_MESSAGE = (
    "I can help connect you to a nurse. If you feel worse or this feels urgent, "
    "seek emergency care. Would you like me to proceed with connecting you?"
)

# Singleton gate instance — stateless, safe to share
_TRANSFER_CONTROL_GATE = TransferControlGate()


def _detect_human_request(utterance: str) -> bool:
    """Detect caller intent to speak with a human. Deterministic, no LLM."""
    if not utterance:
        return False
    for pat in _HUMAN_REQUEST_PATTERNS:
        if pat.search(utterance):
            return True
    return False


# ---------------------------------------------------------------------------
# Part 3: Yes/No Answer Normalisation
# ---------------------------------------------------------------------------

_YES_NO_ACCEPTED: dict[str, str] = {
    # Yes forms
    "yes": "yes", "yeah": "yes", "yep": "yes", "yup": "yes", "ya": "yes",
    "uh huh": "yes", "sure": "yes", "correct": "yes", "right": "yes",
    "absolutely": "yes", "affirmative": "yes", "mhm": "yes", "mm hmm": "yes",
    # No forms
    "no": "no", "nope": "no", "nah": "no", "naw": "no", "negative": "no",
    "not really": "no", "no way": "no",
    # Common STT confusions
    "know": "no", "now": "no",
}


def _normalize_yes_no(utterance: str) -> str | None:
    """Normalize a yes/no utterance. Returns 'yes', 'no', or None if unrecognised."""
    cleaned = utterance.strip().lower().rstrip(".!?,;: ")
    return _YES_NO_ACCEPTED.get(cleaned)


# ---------------------------------------------------------------------------
# Part 4: Retry Ladder (deterministic, non-repetitive)
# ---------------------------------------------------------------------------

_RETRY_LADDER_YES_NO = [
    "Just to confirm, is that yes or no?",
    "I only need yes or no for this one.",
    "I'm having trouble hearing you. Would you like me to connect you with a nurse?",
]

_RETRY_LADDER_DEFAULT = [
    "I'm sorry, I didn't quite catch that. Could you please rephrase?",
    "I want to make sure I understand correctly. Could you try describing it differently?",
    "I'm having trouble hearing you. Would you like me to connect you with a nurse?",
]


def _get_retry_message(retry_index: int, expected_answer_type: str | None) -> str:
    """Return the deterministic retry message for the given retry index.

    retry_index: 0-based (0 = first retry, 1 = second, 2 = third / escalation).
    """
    ladder = _RETRY_LADDER_YES_NO if expected_answer_type == "yes_no" else _RETRY_LADDER_DEFAULT
    idx = min(retry_index, len(ladder) - 1)
    return ladder[idx]


# Unclear-answer signals checked against caller utterance
_UNCLEAR_SIGNALS = [
    r"^\s*$",                              # empty
    r"^(i\s+don\s*'?\s*t\s+know|idk|no\s+idea|not\s+sure|unsure)\s*[\.\?]?$",
    r"^(uh|um|erm|err|hmm+)\s*[\.\?]?$",   # filler only
    r"^(what|huh|pardon|repeat)\s*[\?!]?$",  # confusion signals
    r"^(yes|no|maybe|perhaps|i\s+guess)\s*[\.\?]?$",  # non-answers to open questions
]
_UNCLEAR_PATTERNS = [
    __import__("re").compile(p, __import__("re").IGNORECASE)
    for p in _UNCLEAR_SIGNALS
]

# Escalation messages
_ESCALATION_ER_NOW = (
    "Based on what you've described, this may be a medical emergency. "
    "Please call 9 1 1 or go to the nearest emergency room immediately. "
    "Do not wait."
)
_ESCALATION_URGENT = (
    "Based on what you've shared, we believe you need prompt medical attention today. "
    "Please contact an urgent care clinic or emergency room as soon as possible. "
    "If your symptoms get worse at any time, call 9 1 1."
)
_ESCALATION_LOW_CONFIDENCE = (
    "I want to make sure you get the right care. "
    "Because I need more information to safely assess your situation, "
    "I am connecting you with a nurse who can help you further. "
    "If you feel this is an emergency, please call 9 1 1 now."
)
_ESCALATION_CONFUSED_CALLER = (
    "I'm having difficulty understanding your responses. "
    "I'm going to connect you with a nurse who can better assist you. "
    "If this is an emergency, please call 9 1 1 immediately."
)

# Deterministic patient-facing closing message used when finalize() is
# deferred to the background task.  Using a fixed template avoids blocking
# the Twilio HTTP response on a 9-10 s LLM call, keeping the total round-trip
# well under Twilio's 15-second request timeout.
_FINALIZE_PATIENT_SUMMARY = (
    "Thank you. I now have all the information I need. "
    "A nurse will review your details and contact you very shortly. "
    "In the meantime, if your symptoms worsen suddenly, "
    "please call 9 1 1 or go to your nearest emergency room."
)


# ---------------------------------------------------------------------------
# Low-confidence continue-intake: ordered SBAR follow-up questions
# (Bug 2 fix — used instead of escalation when confidence < threshold but
#  no red flags are present)
# ---------------------------------------------------------------------------

_LOW_CONFIDENCE_FOLLOWUP_QUESTIONS: list[tuple[str, str]] = [
    # (intake_state_field, question_text)
    ("onset_time",       "When did your symptoms first start?"),
    ("symptom_severity", "On a scale of 0 to 10, how severe is your discomfort right now?"),
    ("relevant_history", "Do you have any medical conditions or recent illnesses that may be related?"),
    ("meds",             "Are you currently taking any medications?"),
    ("allergies",        "Do you have any known drug allergies?"),
]

_LOW_CONFIDENCE_FALLBACK_QUESTION = (
    "Can you describe your main symptom in more detail? "
    "For example, where exactly do you feel it, and does it spread anywhere?"
)


def _get_low_confidence_followup(state: "StructuredIntakeState") -> str:
    """Return the next deterministic follow-up question for missing SBAR fields.

    Priority order: onset_time → severity → relevant_history → meds → allergies.
    Falls back to an open-ended descriptor question when all fields are present.
    """
    for field, question in _LOW_CONFIDENCE_FOLLOWUP_QUESTIONS:
        value = getattr(state, field, None)
        # Treat an empty list as missing
        if value is None or value == [] or value == "":
            return question
    return _LOW_CONFIDENCE_FALLBACK_QUESTION


# Map from SBAR slot name → forced-choice loop-breaker wording
_LOOP_BREAKER_QUESTIONS: dict[str, str] = {
    "onset_time": (
        "I need to know roughly when this started. "
        "Would you say it began today, in the last few days, or longer ago?"
    ),
    "symptom_severity": (
        "I need a rough idea of the severity. "
        "Would you say your discomfort is mild, moderate, or severe?"
    ),
    "relevant_history": (
        "Do you have any medical conditions at all — "
        "for example diabetes, heart disease, or high blood pressure? "
        "If none, just say none."
    ),
    "meds": (
        "Are you taking any medications right now? "
        "Prescription, over-the-counter, or supplements? If none, just say none."
    ),
    "allergies": (
        "Do you have any known drug allergies? If none, just say none."
    ),
}


def _get_loop_breaker_question(slot: str | None, original_question: str) -> str:
    """Return forced-choice wording for a slot that has been asked 3+ times."""
    if slot and slot in _LOOP_BREAKER_QUESTIONS:
        return _LOOP_BREAKER_QUESTIONS[slot]
    return original_question


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Multi-agent orchestrator for phone triage.

    Stateless — all state lives in OrchestratorSession.
    """

    def __init__(
        self,
        llm_client=None,
        protocol_retriever: ProtocolRetriever | None = None,
        guarded_llm: GuardedLLM | None = None,
    ) -> None:
        # ALL LLM access goes through GuardedLLM — no direct raw client usage.
        if guarded_llm:
            self._guarded = guarded_llm
        elif llm_client:
            # Testing path: auto-wrap the raw client in GuardedLLM
            self._guarded = GuardedLLM(_client=llm_client)
        else:
            self._guarded = get_guarded_llm()
        self._retriever = protocol_retriever or get_retriever()

    # ------------------------------------------------------------------ #
    # Mode 1: Questioning turn
    # ------------------------------------------------------------------ #

    async def process_turn(
        self,
        session: OrchestratorSession,
        caller_utterance: str,
    ) -> dict:
        """Process one caller turn during the questioning loop.

        Phase 1 Flow:
        0. Pre-check: score_red_flags() — CRITICAL → ER_NOW, score≥10 → URGENT (no LLM)
        1. Confused-caller check: detect unclear answer, retry once, then escalate
        2. Single LLM call → Phase1TurnOutput (2-attempt validation + repair)
        3. Post-check safety gate: scan LLM text for unsafe content
        4. Deterministic confidence scoring (deductions applied to 1.0 base)
        5. If confidence < 0.60 → escalate
        6. Re-run red-flag scoring on updated state
        7. Append decision trace entry
        8. Determine response (ask / escalate / finalize)

        Args:
            session: Current orchestrator session.
            caller_utterance: Raw STT text from the caller.

        Returns:
            Dict with keys:
            - "action": "escalate" | "ask" | "finalize"
            - "message": text to speak to caller
            - "red_flag_result": RedFlagResult (if escalation)
            - "intake_output": IntakeTurnOutput (if ask/finalize)
            - "finalize_output": FinalizeOutput (if finalize)
            - "phase1_output": Phase1TurnOutput (always, when LLM was called)
        """
        correlation_id = str(uuid.uuid4())[:8]
        t0 = time.monotonic()
        metrics = get_metrics()

        # Phase 3: Clear any stale log context from the previous turn/request
        # before injecting fresh values.  Without this, disposition and other
        # fields set in earlier turns bleed into subsequent log entries because
        # FastAPI reuses the same asyncio context between requests.
        clear_log_context()
        set_log_context(
            session_id=session.session_id,
            turn_index=session.turn_count + 1,
        )

        assert session.audit_trace is not None

        session.conversation.append(ConversationTurn(role="caller", text=caller_utterance))
        session.turn_count += 1

        session.audit_trace.add_entry(
            step="turn_start",
            agent="orchestrator",
            input_summary=f"Turn {session.turn_count}: {_redact(caller_utterance, 80)}",
            correlation_id=correlation_id,
        )

        # ── STEP 0: PRE-CHECK SAFETY GATE ────────────────────────────────────
        # Deterministic red-flag scoring: MUST run before any LLM call.
        try:
            rf_disposition, rf_score, rf_triggered_ids = score_red_flags(
                utterance=caller_utterance,
                chief_complaint=session.intake_state.chief_complaint,
                red_flags_reported=session.intake_state.red_flags_reported,
                symptom_severity=session.intake_state.symptom_severity,
                confusion_score=session.intake_state.confusion_or_poor_historian_score,
            )
        except Exception as exc:
            # Fail closed — red-flag logic exception → escalate
            logger.error(
                f"[ORCH:{correlation_id}] Red-flag scoring raised exception: {exc}. "
                "Failing closed."
            )
            session.audit_trace.add_entry(
                step="red_flag_exception",
                agent="safety_rules",
                output_summary=f"Exception: {exc}",
                correlation_id=correlation_id,
            )
            escalation_msg = _ESCALATION_ER_NOW
            session.conversation.append(ConversationTurn(role="assistant", text=escalation_msg))
            session.is_finalized = True
            self._append_trace(
                session=session,
                user_text=caller_utterance,
                confidence=0.0,
                disposition="HUMAN_REVIEW",
                escalation_required=True,
                response=escalation_msg,
                red_flags=[f"exception:{exc}"],
                rules=["fail_closed:red_flag_exception"],
            )
            metrics.triage_escalations_total.inc()
            metrics.red_flag_triggers_total.inc()
            return {
                "action": "escalate",
                "message": escalation_msg,
                "fail_reason": f"red_flag_exception:{exc}",
            }

        # Pre-check hard stop: ER_NOW
        if rf_disposition == "ER_NOW":
            logger.warning(
                f"[ORCH:{correlation_id}] PRE-CHECK ER_NOW: "
                f"triggered={rf_triggered_ids}, score={rf_score}"
            )
            # Also run legacy check_red_flags to get caller-friendly script
            legacy_result = check_red_flags(
                utterance=caller_utterance,
                chief_complaint=session.intake_state.chief_complaint,
                red_flags_reported=session.intake_state.red_flags_reported,
                symptom_severity=session.intake_state.symptom_severity,
                confusion_score=session.intake_state.confusion_or_poor_historian_score,
            )
            escalation_msg = (
                legacy_result.script_to_say
                if (legacy_result.triggered and legacy_result.script_to_say)
                else _ESCALATION_ER_NOW
            )
            session.audit_trace.add_entry(
                step="pre_check_er_now",
                agent="safety_rules",
                output_summary=f"ER_NOW: {rf_triggered_ids}",
                correlation_id=correlation_id,
            )
            session.audit_trace.deterministic_rules_triggered.extend(rf_triggered_ids)
            session.safety_flags.append(SafetyFlag(
                source="deterministic",
                level=SafetyLevel.EMERGENT,
                flag=", ".join(rf_triggered_ids),
                reason_for_audit="Phase1 pre-check: critical red flag",
                script_to_say=escalation_msg,
            ))
            session.conversation.append(ConversationTurn(role="assistant", text=escalation_msg))
            session.is_finalized = True
            self._append_trace(
                session=session,
                user_text=caller_utterance,
                confidence=0.0,
                disposition="ER_NOW",
                escalation_required=True,
                response=escalation_msg,
                red_flags=rf_triggered_ids,
                rules=["pre_check:critical_flag"],
            )
            metrics.triage_escalations_total.inc()
            metrics.red_flag_triggers_total.inc(len(rf_triggered_ids) or 1)
            return {
                "action": "escalate",
                "message": escalation_msg,
                "red_flag_result": legacy_result if legacy_result.triggered else RedFlagResult(
                    triggered=True,
                    level=SafetyLevel.EMERGENT,
                    matched_rules=rf_triggered_ids,
                    reason_for_audit="Phase1 pre-check: critical red flag",
                ),
            }

        # Pre-check hard stop: URGENT (score ≥ 10)
        if rf_disposition == "URGENT":
            logger.warning(
                f"[ORCH:{correlation_id}] PRE-CHECK URGENT: "
                f"triggered={rf_triggered_ids}, score={rf_score}"
            )
            escalation_msg = _ESCALATION_URGENT
            session.audit_trace.add_entry(
                step="pre_check_urgent",
                agent="safety_rules",
                output_summary=f"URGENT: score={rf_score}, flags={rf_triggered_ids}",
                correlation_id=correlation_id,
            )
            session.audit_trace.deterministic_rules_triggered.extend(rf_triggered_ids)
            session.safety_flags.append(SafetyFlag(
                source="deterministic",
                level=SafetyLevel.URGENT,
                flag=f"score={rf_score}: " + ", ".join(rf_triggered_ids),
                reason_for_audit="Phase1 pre-check: weighted score >= 10",
                script_to_say=escalation_msg,
            ))
            session.conversation.append(ConversationTurn(role="assistant", text=escalation_msg))
            session.is_finalized = True
            self._append_trace(
                session=session,
                user_text=caller_utterance,
                confidence=0.0,
                disposition="URGENT",
                escalation_required=True,
                response=escalation_msg,
                red_flags=rf_triggered_ids,
                rules=[f"pre_check:score={rf_score}"],
            )
            metrics.triage_escalations_total.inc()
            metrics.red_flag_triggers_total.inc(len(rf_triggered_ids) or 1)
            return {
                "action": "escalate",
                "message": escalation_msg,
                "red_flag_result": RedFlagResult(
                    triggered=True,
                    level=SafetyLevel.URGENT,
                    matched_rules=rf_triggered_ids,
                    reason_for_audit=f"Phase1 pre-check: weighted score={rf_score} >= 10",
                ),
            }

        # ── STEP 0.5: TRANSFER CONTROL GATE (replaces old human-request short-circuit)
        # ─────────────────────────────────────────────────────────
        # Deterministic red-flag check (STEP 0) already ran above.  If we reach
        # here, rf_disposition is NOT ER_NOW / URGENT.  We can now safely evaluate
        # whether a nurse-request utterance should be honoured or blocked.
        if _detect_human_request(caller_utterance):
            logger.info(
                f"[ORCH:{correlation_id}] Nurse/human transfer request detected — "
                "evaluating transfer control gate."
            )
            gate_decision = _TRANSFER_CONTROL_GATE.evaluate(
                session=session,
                next_question="",         # gate derives from missing fields when empty
                red_flags_triggered=False, # confirmed above; pre-check would have exited
            )

            if gate_decision.action in ("redirect", "allow_transfer", "premature_transfer"):
                gate_msg = gate_decision.message
                is_escalating = gate_decision.action in ("allow_transfer", "premature_transfer")

                if is_escalating and not gate_msg:
                    gate_msg = _HUMAN_REQUEST_MESSAGE

                if is_escalating:
                    session.conversation.append(ConversationTurn(role="assistant", text=gate_msg))
                    session.is_finalized = True

                session.audit_trace.add_entry(
                    step="transfer_control_gate",
                    agent="intake_gate",
                    output_summary=(
                        f"action={gate_decision.action}, "
                        f"intake_complete={gate_decision.intake_complete}, "
                        f"resistance={gate_decision.resistance_count}, "
                        f"reason={gate_decision.transfer_reason}"
                    ),
                    correlation_id=correlation_id,
                )

                self._append_trace(
                    session=session,
                    user_text=caller_utterance,
                    confidence=max(0.0, 1.0 + gate_decision.confidence_delta),
                    disposition="HUMAN_REVIEW",
                    escalation_required=is_escalating,
                    response=gate_msg,
                    red_flags=[],
                    rules=[gate_decision.transfer_reason or "transfer_control_gate"],
                    intake_complete=gate_decision.intake_complete,
                    premature_transfer=gate_decision.premature,
                    resistance_count=gate_decision.resistance_count,
                    transfer_reason=gate_decision.transfer_reason,
                    override_applied=gate_decision.override_applied,
                )

                if is_escalating:
                    return {
                        "action": "escalate",
                        "message": gate_msg,
                        "premature_transfer": gate_decision.premature,
                        "intake_complete": gate_decision.intake_complete,
                        "resistance_count": gate_decision.resistance_count,
                    }
                else:
                    # Blocked: continue intake with redirection message
                    session.conversation.append(
                        ConversationTurn(role="assistant", text=gate_msg)
                    )
                    session.last_expected_answer_type = "free_text"
                    return {
                        "action": "ask",
                        "message": gate_msg,
                        "override_applied": True,
                        "intake_complete": False,
                        "resistance_count": gate_decision.resistance_count,
                    }

        # ── STEP 1: CONFUSED-CALLER PROTOCOL (with retry ladder) ─────────
        expected = session.last_expected_answer_type
        is_unclear = self._is_unclear_answer(caller_utterance, expected_answer_type=expected)
        if is_unclear:
            session.unclear_answer_retries += 1
            retry_count = session.unclear_answer_retries
            logger.info(
                f"[ORCH:{correlation_id}] Unclear answer detected "
                f"(retry #{retry_count}, expected={expected})"
            )
            if retry_count >= 3:
                # Third unclear attempt → escalate with nurse offer
                escalation_msg = _get_retry_message(2, expected)
                session.conversation.append(ConversationTurn(role="assistant", text=escalation_msg))
                session.is_finalized = True
                session.audit_trace.add_entry(
                    step="confused_caller_escalation",
                    agent="orchestrator",
                    output_summary=f"Three unclear answers — escalating (expected={expected})",
                    correlation_id=correlation_id,
                )
                self._append_trace(
                    session=session,
                    user_text=caller_utterance,
                    confidence=0.0,
                    disposition="HUMAN_REVIEW",
                    escalation_required=True,
                    response=escalation_msg,
                    red_flags=[],
                    rules=[f"confused_caller:retry_{retry_count}_escalate"],
                )
                return {
                    "action": "escalate",
                    "message": escalation_msg,
                    "fail_reason": "confused_caller_max_retries",
                }
            else:
                # Use deterministic retry ladder (non-repetitive)
                retry_msg = _get_retry_message(retry_count - 1, expected)
                session.conversation.append(ConversationTurn(role="assistant", text=retry_msg))
                session.audit_trace.add_entry(
                    step="unclear_answer_retry",
                    agent="orchestrator",
                    output_summary=f"Unclear answer, retry {retry_count} (expected={expected})",
                    correlation_id=correlation_id,
                )
                self._append_trace(
                    session=session,
                    user_text=caller_utterance,
                    confidence=0.5,
                    disposition="HUMAN_REVIEW",
                    escalation_required=False,
                    response=retry_msg,
                    red_flags=[],
                    rules=[f"confused_caller:retry_{retry_count}"],
                )
                return {
                    "action": "ask",
                    "message": retry_msg,
                }
        else:
            # Clear answer: reset retry counter
            session.unclear_answer_retries = 0

        # ── STEP 1.5: PROTOCOL RETRIEVAL (Phase 2) ───────────────────────────
        # Retrieve relevant protocol snippets before LLM call.
        # Failure → empty list, never crashes the system.
        protocol_snippets: list[ProtocolSnippet] = []
        protocol_hits: list[ProtocolHit] = []
        try:
            entities_dict = session.intake_state.model_dump(
                exclude_none=True, exclude_defaults=True,
            )
            protocol_snippets = self._retriever.retrieve(
                chief_complaint=session.intake_state.chief_complaint,
                recent_utterance=caller_utterance,
                extracted_entities=entities_dict,
                red_flags=rf_triggered_ids if rf_triggered_ids else None,
            )
            if protocol_snippets:
                protocol_hits = [
                    ProtocolHit(id=s.id, title=s.title, version=s.version)
                    for s in protocol_snippets
                ]
                logger.info(
                    f"[ORCH:{correlation_id}] Protocol hits: "
                    f"{[h.id for h in protocol_hits]}"
                )
                metrics.retriever_hits_total.inc(len(protocol_hits))
                session.audit_trace.add_entry(
                    step="protocol_retrieval",
                    agent="protocol_retriever",
                    output_summary=(
                        f"hits={[h.id for h in protocol_hits]}, "
                        f"scores={[f'{s.score:.1f}' for s in protocol_snippets]}"
                    ),
                    correlation_id=correlation_id,
                )
        except Exception as exc:
            logger.warning(
                f"[ORCH:{correlation_id}] Protocol retrieval failed: {exc}. "
                "Continuing without protocol context."
            )
            protocol_snippets = []
            protocol_hits = []

        # ── STEP 2: CONCURRENT LLM CALLS — Phase1 (safety/decision) +
        # IntakeTurn (question generation).  Both message sets are built from
        # the same session state (neither call mutates it), so firing them in
        # parallel is safe.  Total latency = max(phase1, intake) instead of
        # phase1 + intake, saving ~3 s on every dynamic turn.
        from src.config import STORE_PHI as _STORE_PHI_P1
        messages        = self._build_phase1_messages(session, protocol_snippets=protocol_snippets)
        intake_messages = self._build_intake_messages(session, protocol_snippets=protocol_snippets)

        phase1_output: Optional[Phase1TurnOutput] = None
        llm_repair_used: bool = False

        # Gate contexts
        phase1_gate_ctx = GateContext(
            session_id=session.session_id,
            caller_utterance=caller_utterance,
            chief_complaint=session.intake_state.chief_complaint,
            red_flags_reported=session.intake_state.red_flags_reported,
            symptom_severity=session.intake_state.symptom_severity,
            confusion_score=session.intake_state.confusion_or_poor_historian_score,
            store_phi=_STORE_PHI_P1,
        )
        intake_gate_ctx = GateContext(
            session_id=session.session_id,
            caller_utterance=caller_utterance,
            chief_complaint=session.intake_state.chief_complaint,
            red_flags_reported=session.intake_state.red_flags_reported,
            symptom_severity=session.intake_state.symptom_severity,
            confusion_score=session.intake_state.confusion_or_poor_historian_score,
            store_phi=_STORE_PHI_P1,
        )

        # ── Concurrent Phase1 + IntakeTurn ───────────────────────────────────
        _p1_or_exc, _intake_or_exc = await asyncio.gather(
            self._guarded.structured_call(
                messages=messages,
                output_schema=Phase1TurnOutput,
                ctx=phase1_gate_ctx,
                kind="phase1_reply",
                max_tokens=400,
                temperature=0.1,
                correlation_id=correlation_id,
            ),
            self._guarded.structured_call(
                messages=intake_messages,
                output_schema=IntakeTurnOutput,
                ctx=intake_gate_ctx,
                kind="question",
                max_tokens=400,
                temperature=0.1,
                correlation_id=correlation_id,
            ),
            return_exceptions=True,
        )

        # ── Handle Phase1 result ──────────────────────────────────────────────
        if isinstance(_p1_or_exc, LLMCallError):
            exc = _p1_or_exc
            # Schema validation failed after repair, OR LLM unreachable → fail closed
            logger.error(f"[ORCH:{correlation_id}] Phase1 structured call failed: {exc}")
            session.audit_trace.add_entry(
                step="phase1_structured_call_failed",
                agent="phase1_agent",
                output_summary=f"structured_call failure: {exc}",
                correlation_id=correlation_id,
            )
            escalation_msg = _ESCALATION_LOW_CONFIDENCE
            session.conversation.append(ConversationTurn(role="assistant", text=escalation_msg))
            session.is_finalized = True
            self._append_trace(
                session=session,
                user_text=caller_utterance,
                confidence=0.0,
                disposition="HUMAN_REVIEW",
                escalation_required=True,
                response=escalation_msg,
                red_flags=[],
                rules=[f"fail_closed:structured_call:{exc}"],
            )
            metrics.llm_timeouts_total.inc()
            metrics.triage_escalations_total.inc()
            return {
                "action": "escalate",
                "message": escalation_msg,
                "fail_reason": f"structured_call_failed:{exc}",
            }
        if isinstance(_p1_or_exc, BaseException):
            exc = _p1_or_exc
            # Any other failure → fail closed
            logger.error(f"[ORCH:{correlation_id}] Phase1 LLM call failed: {exc}")
            session.audit_trace.add_entry(
                step="phase1_llm_timeout",
                agent="phase1_agent",
                output_summary=f"LLM failure: {exc}",
                correlation_id=correlation_id,
            )
            escalation_msg = _ESCALATION_LOW_CONFIDENCE
            session.conversation.append(ConversationTurn(role="assistant", text=escalation_msg))
            session.is_finalized = True
            self._append_trace(
                session=session,
                user_text=caller_utterance,
                confidence=0.0,
                disposition="HUMAN_REVIEW",
                escalation_required=True,
                response=escalation_msg,
                red_flags=[],
                rules=[f"fail_closed:llm_timeout:{exc}"],
            )
            metrics.llm_timeouts_total.inc()
            metrics.triage_escalations_total.inc()
            return {
                "action": "escalate",
                "message": escalation_msg,
                "fail_reason": f"llm_timeout:{exc}",
            }

        phase1_output = _p1_or_exc

        # ── STEP 3: POST-CHECK SAFETY GATE ───────────────────────────────────
        # Determine previous disposition for downgrade detection
        previous_disposition: Optional[str] = None
        if session.decision_trace:
            previous_disposition = session.decision_trace[-1].disposition

        try:
            post_check_safety_gate(
                llm_response_text=phase1_output.model_dump_json(),
                previous_disposition=previous_disposition,
                phase1_output=phase1_output,
                correlation_id=correlation_id,
            )
        except PostCheckViolation as pcv:
            logger.error(
                f"[ORCH:{correlation_id}] POST-CHECK VIOLATION: {pcv.reason}"
            )
            session.audit_trace.add_entry(
                step="post_check_violation",
                agent="safety_gates",
                output_summary=f"Violation: {pcv.reason}",
                correlation_id=correlation_id,
            )
            # Override with safe escalation
            phase1_output = safe_phase1_escalation(reason=f"post_check:{pcv.reason}")
            escalation_msg = _ESCALATION_LOW_CONFIDENCE
            session.conversation.append(ConversationTurn(role="assistant", text=escalation_msg))
            session.is_finalized = True
            self._append_trace(
                session=session,
                user_text=caller_utterance,
                confidence=0.0,
                disposition="HUMAN_REVIEW",
                escalation_required=True,
                response=escalation_msg,
                red_flags=[],
                rules=[f"fail_closed:post_check:{pcv.reason}"],
            )
            metrics.post_check_violations_total.inc()
            metrics.triage_escalations_total.inc()
            return {
                "action": "escalate",
                "message": escalation_msg,
                "fail_reason": f"post_check_violation:{pcv.reason}",
                "phase1_output": phase1_output,
            }

        # ── STEP 4: DETERMINISTIC CONFIDENCE SCORING ─────────────────────────
        confidence_bd = ConfidenceBreakdown()

        state = session.intake_state
        if (
            state.caller_age is None
            or not state.chief_complaint
            or not state.onset_time
        ):
            confidence_bd.apply("missing_key_info(age|chief_complaint|onset_time)", 0.15)

        # Detect contradictions: severity=mild + LLM flags severe
        if (
            state.symptom_severity == "mild"
            and phase1_output.red_flags_triggered
        ):
            confidence_bd.apply("contradiction:mild_severity_with_red_flags", 0.20)

        # Unclear answer penalised
        if is_unclear:
            confidence_bd.apply("unclear_answer_this_turn", 0.15)

        # Repair penalty
        if llm_repair_used:
            confidence_bd.apply("llm_output_required_repair", 0.20)

        # Ambiguous red flags
        if rf_triggered_ids and rf_score < 10 and not any(
            p.search(caller_utterance.lower()) for p in _UNCLEAR_PATTERNS
        ):
            # Weighted flags hit but score below URGENT — ambiguous territory
            confidence_bd.apply(
                f"ambiguous_weighted_flags(score={rf_score},could_not_clarify)", 0.30
            )

        final_confidence = confidence_bd.clamp_and_finalise()

        # Phase 3: Observe confidence score
        metrics.confidence_score.observe(final_confidence)
        set_log_context(disposition=phase1_output.disposition.value)

        logger.debug(
            f"[ORCH:{correlation_id}] Confidence: {final_confidence:.2f} "
            f"deductions={[d.reason for d in confidence_bd.deductions]}"
        )

        # ── STEP 5: LOW-CONFIDENCE GATE ──────────────────────────────────────
        # Policy (Bug 2 fix):
        #   • Red flags detected (pre-check OR LLM) → escalate as before.
        #     Red-flag supremacy must never be bypassed.
        #   • No red flags + low confidence → continue intake with a
        #     deterministic follow-up question that fills the next missing
        #     SBAR field.  This prevents premature nurse transfers that cut
        #     off data collection before a clinical picture has formed.
        if final_confidence < PHASE1_CONFIDENCE_ESCALATION_THRESHOLD:
            # Require a meaningful red-flag signal before co-escalating with low
            # confidence.  A single lightweight weighted flag (score < 8) is not
            # sufficient on its own — it might reflect a false-positive pattern
            # match.  Critical flags are already short-circuited in Step 0.
            any_red_flags = (rf_score >= 8) or bool(phase1_output.red_flags_triggered)

            if any_red_flags:
                # Red flags present — escalate (original behaviour preserved)
                logger.warning(
                    f"[ORCH:{correlation_id}] Confidence {final_confidence:.2f} < "
                    f"{PHASE1_CONFIDENCE_ESCALATION_THRESHOLD} AND red flags present "
                    f"({rf_triggered_ids + phase1_output.red_flags_triggered}) — escalating"
                )
                session.audit_trace.add_entry(
                    step="low_confidence_escalation",
                    agent="confidence_engine",
                    output_summary=(
                        f"confidence={final_confidence:.2f}, "
                        f"red_flags={rf_triggered_ids + phase1_output.red_flags_triggered}, "
                        f"deductions={[d.reason for d in confidence_bd.deductions]}"
                    ),
                    correlation_id=correlation_id,
                )
                escalation_msg = _ESCALATION_LOW_CONFIDENCE
                session.conversation.append(ConversationTurn(role="assistant", text=escalation_msg))
                session.is_finalized = True
                self._append_trace(
                    session=session,
                    user_text=caller_utterance,
                    confidence=final_confidence,
                    disposition=phase1_output.disposition.value,
                    escalation_required=True,
                    response=escalation_msg,
                    red_flags=rf_triggered_ids + phase1_output.red_flags_triggered,
                    rules=phase1_output.rules_triggered,
                    confidence_breakdown=confidence_bd,
                )
                return {
                    "action": "escalate",
                    "message": escalation_msg,
                    "fail_reason": f"low_confidence_with_red_flags:{final_confidence:.2f}",
                    "phase1_output": phase1_output,
                }

            else:
                # No red flags — do NOT escalate; fill next missing SBAR field.
                # CRITICAL: resolve the concurrent intake call and apply
                # extracted fields BEFORE choosing the follow-up question,
                # otherwise the session state never updates and we loop on
                # the same missing slot forever.
                logger.info(
                    f"[ORCH:{correlation_id}] Confidence {final_confidence:.2f} < "
                    f"{PHASE1_CONFIDENCE_ESCALATION_THRESHOLD} but NO red flags — "
                    "continuing intake (LOW_CONFIDENCE_CONTINUE_INTAKE)"
                )

                # ── Resolve intake result (mirrors Steps 6-8 logic) ──────
                if isinstance(_intake_or_exc, BaseException):
                    logger.error(
                        f"[ORCH:{correlation_id}] Intake LLM call failed in "
                        f"low-confidence branch: {_intake_or_exc}"
                    )
                    intake_output = self._get_fallback_intake(session)
                    session.audit_trace.add_entry(
                        step="llm_fallback",
                        agent="intake_agent",
                        output_summary="Using fallback question due to LLM failure (low-confidence branch)",
                        correlation_id=correlation_id,
                    )
                else:
                    intake_output = _intake_or_exc

                # Apply extracted fields so session.intake_state reflects
                # what the caller just told us.
                self._apply_extracted_fields(session, intake_output)

                # Now choose the follow-up based on the UPDATED state.
                follow_up = _get_low_confidence_followup(session.intake_state)

                # ── Loop-breaker: detect repeated follow-up on same slot ──
                current_slot = self._detect_followup_slot(follow_up)
                if current_slot and current_slot == session.last_followup_slot:
                    session.followup_repeat_count += 1
                else:
                    session.last_followup_slot = current_slot
                    session.followup_repeat_count = 1

                if session.followup_repeat_count >= 3:
                    # Slot asked 3+ times without being filled — use
                    # forced-choice wording to break the loop.
                    follow_up = _get_loop_breaker_question(current_slot, follow_up)
                    logger.info(
                        f"[ORCH:{correlation_id}] Loop-breaker activated "
                        f"for slot={current_slot} after {session.followup_repeat_count} repeats"
                    )

                session.audit_trace.add_entry(
                    step="low_confidence_continue_intake",
                    agent="confidence_engine",
                    output_summary=(
                        f"confidence={final_confidence:.2f}, "
                        f"override=LOW_CONFIDENCE_CONTINUE_INTAKE, "
                        f"follow_up={follow_up[:60]}"
                    ),
                    correlation_id=correlation_id,
                )
                session.conversation.append(ConversationTurn(role="assistant", text=follow_up))
                self._append_trace(
                    session=session,
                    user_text=caller_utterance,
                    confidence=final_confidence,
                    disposition="CONTINUE_INTAKE",
                    escalation_required=False,
                    response=follow_up,
                    red_flags=[],
                    rules=["low_confidence:continue_intake"],
                    confidence_breakdown=confidence_bd,
                    override_applied=True,
                    low_confidence_reprompt=True,
                    override_reason="LOW_CONFIDENCE_CONTINUE_INTAKE",
                )
                return {
                    "action": "ask",
                    "message": follow_up,
                    "low_confidence_reprompt": True,
                    "intake_complete": False,
                    "override_applied": "LOW_CONFIDENCE_CONTINUE_INTAKE",
                    "phase1_output": phase1_output,
                    "intake_output": intake_output,
                }

        # ── STEPS 6-8: INTAKE STATE UPDATE + TRACE ───────────────────────────
        # IntakeTurn already completed concurrently in Step 2.  Resolve the
        # result (or fall back gracefully on failure).
        if isinstance(_intake_or_exc, BaseException):
            logger.error(
                f"[ORCH:{correlation_id}] Intake LLM call failed: {_intake_or_exc}"
            )
            intake_output = self._get_fallback_intake(session)
            session.audit_trace.add_entry(
                step="llm_fallback",
                agent="intake_agent",
                output_summary="Using fallback question due to LLM failure",
                correlation_id=correlation_id,
            )
        else:
            intake_output = _intake_or_exc
            _conf = getattr(intake_output, 'confidence', None)
            _nq   = getattr(intake_output, 'next_question', '') or ''
            logger.info(
                f"[ORCH:{correlation_id}] IntakeTurn: confidence={_conf}, "
                f"next_q={_nq[:60]}..."
            )

        elapsed = (time.monotonic() - t0) * 1000

        # Phase 3: Observe turn latency
        metrics.turn_latency_ms.observe(elapsed)

        # Apply extracted fields to state
        self._apply_extracted_fields(session, intake_output)

        # Record LLM safety flags
        for sf in intake_output.llm_safety_flags:
            session.safety_flags.append(sf)

        if intake_output.safety_flags_coerced:
            session.add_coercion("llm_safety_flags_coerced_from_strings")
            session.audit_trace.add_entry(
                step="coercion",
                agent="llm_parse",
                output_summary="Coerced llm_safety_flags from strings to SafetyFlag objects",
                correlation_id=correlation_id,
            )

        # Re-run red-flag scoring on updated state
        try:
            post_rf_disp, post_rf_score, post_rf_ids = score_red_flags(
                utterance="",
                chief_complaint=session.intake_state.chief_complaint,
                red_flags_reported=session.intake_state.red_flags_reported,
                symptom_severity=session.intake_state.symptom_severity,
                confusion_score=session.intake_state.confusion_or_poor_historian_score,
            )
        except Exception:
            post_rf_disp, _post_rf_score, post_rf_ids = "HUMAN_REVIEW", 0, []

        if post_rf_disp == "ER_NOW":
            session.audit_trace.deterministic_rules_triggered.extend(post_rf_ids)
            escalation_msg = _ESCALATION_ER_NOW
            session.conversation.append(ConversationTurn(role="assistant", text=escalation_msg))
            session.is_finalized = True
            self._append_trace(
                session=session,
                user_text=caller_utterance,
                confidence=final_confidence,
                disposition="ER_NOW",
                escalation_required=True,
                response=escalation_msg,
                red_flags=post_rf_ids,
                rules=["post_state_check:critical_flag"],
                confidence_breakdown=confidence_bd,
            )
            return {
                "action": "escalate",
                "message": escalation_msg,
                "phase1_output": phase1_output,
            }

        session.audit_trace.add_entry(
            step="intake_turn_complete",
            agent="intake_agent",
            output_summary=(
                f"p1_confidence={final_confidence:.2f}, "
                f"disposition={phase1_output.disposition.value}, "
                f"repair={llm_repair_used}"
            ),
            duration_ms=elapsed,
            correlation_id=correlation_id,
        )

        # Determine next action
        should_finalize = self._should_finalize(session, intake_output)

        # Append decision trace entry
        self._append_trace(
            session=session,
            user_text=caller_utterance,
            confidence=final_confidence,
            disposition=phase1_output.disposition.value,
            escalation_required=phase1_output.escalation_required,
            response=intake_output.next_question if not should_finalize else "[finalize]",
            red_flags=rf_triggered_ids + phase1_output.red_flags_triggered,
            rules=phase1_output.rules_triggered,
            confidence_breakdown=confidence_bd,
            extracted_entities={
                k: v for k, v in
                intake_output.extracted_fields_update.model_dump(exclude_none=True).items()
            },
            protocol_hits=protocol_hits,
            protocol_citations=[h.id for h in protocol_hits],
        )

        if should_finalize:
            # Do NOT call finalize() inline — a 9-10 s LLM round-trip here
            # pushes total Twilio HTTP response time past the 15-second timeout,
            # causing the call to be silently disconnected.  Instead, return a
            # deterministic safe summary now and let the route's background task
            # call finalize() for the SBAR report.
            session.is_finalized = True
            return {
                "action": "finalize",
                "message": _FINALIZE_PATIENT_SUMMARY,
                "intake_output": intake_output,
                "phase1_output": phase1_output,
            }

        session.conversation.append(ConversationTurn(
            role="assistant",
            text=intake_output.next_question,
        ))

        # Part 3: Store expected answer type for next turn's unclear detection
        session.last_expected_answer_type = getattr(
            intake_output, "expected_answer_type", "free_text"
        )

        return {
            "action": "ask",
            "message": intake_output.next_question,
            "intake_output": intake_output,
            "phase1_output": phase1_output,
        }

    # ------------------------------------------------------------------ #
    # Mode 2: Finalize
    # ------------------------------------------------------------------ #

    async def finalize(
        self,
        session: OrchestratorSession,
        correlation_id: str | None = None,
    ) -> FinalizeOutput:
        """Run finalization: clinical reasoning + SBAR generation.

        Uses 1 LLM call. If the session was already escalated by deterministic
        rules, produces an emergency SBAR.

        Args:
            session: Current orchestrator session.
            correlation_id: Optional correlation ID for logging.

        Returns:
            FinalizeOutput with disposition, SBAR, and patient summary.
        """
        cid = correlation_id or str(uuid.uuid4())[:8]
        t0 = time.monotonic()

        # Guaranteed non-None by model_post_init
        assert session.audit_trace is not None

        session.audit_trace.add_entry(
            step="finalize_start",
            agent="orchestrator",
            correlation_id=cid,
        )

        # If deterministic rules already triggered, produce emergency output directly
        if session.audit_trace.deterministic_rules_triggered:
            logger.info(
                f"[ORCH:{cid}] Deterministic rules were triggered — "
                "producing emergency SBAR via LLM"
            )

        messages = self._build_finalize_messages(session)

        # Build gate context for finalize
        from src.config import STORE_PHI as _STORE_PHI_FIN
        finalize_gate_ctx = GateContext(
            session_id=session.session_id,
            caller_utterance="",
            chief_complaint=session.intake_state.chief_complaint,
            red_flags_reported=session.intake_state.red_flags_reported,
            symptom_severity=session.intake_state.symptom_severity,
            confusion_score=session.intake_state.confusion_or_poor_historian_score,
            store_phi=_STORE_PHI_FIN,
        )

        try:
            finalize_output = await self._guarded.structured_call(
                messages=messages,
                output_schema=FinalizeOutput,
                ctx=finalize_gate_ctx,
                kind="handoff",
                max_tokens=800,
                temperature=0.3,
                correlation_id=cid,
            )
        except LLMCallError as e:
            logger.error(f"[ORCH:{cid}] Finalize LLM call failed: {e}")
            finalize_output = safe_finalize_default()
        except _PydanticValidationError as e:
            logger.error(
                f"[ORCH:{cid}] Finalize validation failed — "
                f"falling back to HUMAN_REVIEW: {e}"
            )
            finalize_output = safe_finalize_default()
        except Exception as e:
            logger.error(
                f"[ORCH:{cid}] Unexpected error during finalize — "
                f"falling back to HUMAN_REVIEW: {e}"
            )
            finalize_output = safe_finalize_default()

        # Tweak #3: detect if coercion happened during finalize
        if finalize_output.safety_flags_coerced:
            session.add_coercion("llm_safety_flags_coerced_from_strings")
            session.audit_trace.add_entry(
                step="coercion",
                agent="llm_parse",
                output_summary="Coerced llm_safety_flags from strings to SafetyFlag objects",
                correlation_id=cid,
            )

        # ── UNIFIED SAFETY GATE — finalize output MUST pass through ──
        # structured_call() already gated text fields via gate_outbound_text.
        # Now run gate_triage_output for disposition validation + red-flag override.
        from src.config import DEEPSEEK_MODEL
        gate_input = {
            "disposition": finalize_output.disposition.value,
            "urgency_level": "CRITICAL" if finalize_output.disposition == DispositionCategory.ER_NOW else "HIGH",
            "confidence_score": 0.8,  # Finalize assumes sufficient data
            "rules_triggered": [],
            "red_flags_triggered": [f.flag for f in finalize_output.llm_safety_flags],
            "escalation_required": finalize_output.disposition in (DispositionCategory.ER_NOW, DispositionCategory.HUMAN_REVIEW),
            "protocol_references": [h.id for h in (session.decision_trace[-1].protocol_hits if session.decision_trace else [])],
            "model_version": DEEPSEEK_MODEL,
            "message_to_caller": finalize_output.patient_summary,
        }
        gate_decision: FinalDecision = gate_triage_output(gate_input, finalize_gate_ctx)
        logger.info(
            f"[ORCH:{cid}] Safety gate finalize: disposition={gate_decision.disposition}, "
            f"escalation={gate_decision.escalation_required}, "
            f"rewrites={len(gate_decision.diagnosis_rewrites)}"
        )

        # Apply gate overrides
        if gate_decision.escalation_required and finalize_output.disposition not in (
            DispositionCategory.ER_NOW, DispositionCategory.HUMAN_REVIEW,
        ):
            finalize_output.disposition = DispositionCategory.HUMAN_REVIEW
            finalize_output.disposition_reasoning = (
                f"Safety gate override: {gate_decision.gate_trace[-1] if gate_decision.gate_trace else 'unknown'}. "
                f"Original: {finalize_output.disposition_reasoning}"
            )

        # Deterministic override: if red flags were triggered, force ER_NOW
        if session.audit_trace.deterministic_rules_triggered:
            if finalize_output.disposition != DispositionCategory.ER_NOW:
                logger.warning(
                    f"[ORCH:{cid}] Overriding LLM disposition "
                    f"{finalize_output.disposition} → ER_NOW (deterministic rules)"
                )
                add_safety_gate_breadcrumb(
                    rule_name=", ".join(session.audit_trace.deterministic_rules_triggered),
                    disposition_override="ER_NOW",
                )
                finalize_output.disposition = DispositionCategory.ER_NOW
                finalize_output.disposition_reasoning = (
                    f"Deterministic safety rules triggered: "
                    f"{', '.join(session.audit_trace.deterministic_rules_triggered)}. "
                    f"Original LLM reasoning: {finalize_output.disposition_reasoning}"
                )

        elapsed = (time.monotonic() - t0) * 1000

        # ── INVARIANT: disposition must be canonical ──
        from src.shared.canonical import assert_canonical
        assert_canonical(finalize_output.disposition.value)

        session.is_finalized = True
        session.finalize_output = finalize_output
        session.audit_trace.final_disposition = finalize_output.disposition

        session.audit_trace.add_entry(
            step="finalize_complete",
            agent="clinical_reasoning",
            output_summary=(
                f"disposition={finalize_output.disposition.value}, "
                f"reasoning={finalize_output.disposition_reasoning[:80]}"
            ),
            duration_ms=elapsed,
            correlation_id=cid,
        )

        return finalize_output

    # ------------------------------------------------------------------ #
    # Message builders
    # ------------------------------------------------------------------ #

    def _build_intake_messages(self, session: OrchestratorSession, protocol_snippets: list[ProtocolSnippet] | None = None) -> list[dict]:
        """Build the message list for an intake turn LLM call."""
        messages: list[dict] = [
            {"role": "system", "content": INTAKE_TURN_SYSTEM_PROMPT},
        ]

        # Add patient context
        state = session.intake_state
        context_parts = []
        if state.caller_name:
            context_parts.append(f"Name: {state.caller_name}")
        if state.caller_age is not None:
            context_parts.append(f"Age: {state.caller_age}")
        if state.caller_sex:
            context_parts.append(f"Sex: {state.caller_sex}")
        if state.chief_complaint:
            context_parts.append(f"Chief complaint: {state.chief_complaint}")

        if context_parts:
            messages.append({
                "role": "system",
                "content": f"Patient demographics: {', '.join(context_parts)}",
            })

        # Add current state summary
        state_dict = state.model_dump(exclude_none=True, exclude_defaults=True)
        if state_dict:
            messages.append({
                "role": "system",
                "content": (
                    f"Current intake state (already collected):\n"
                    f"{json.dumps(state_dict, indent=2)}"
                ),
            })

        # Phase 2: Inject protocol context if available
        if protocol_snippets:
            messages.append({
                "role": "system",
                "content": self._format_protocol_context(protocol_snippets),
            })

        # Turn count reminder
        messages.append({
            "role": "system",
            "content": (
                f"Turn {session.turn_count}/{session.max_turns}. "
                f"Stop early if you have enough information."
            ),
        })

        # Add conversation history (last 15 for context window)
        messages.extend([t.to_llm_message() for t in session.conversation[-15:]])

        return messages

    def _build_finalize_messages(self, session: OrchestratorSession) -> list[dict]:
        """Build the message list for the finalize LLM call."""
        assert session.audit_trace is not None

        messages: list[dict] = [
            {"role": "system", "content": FINALIZE_SYSTEM_PROMPT},
        ]

        # Add full patient state
        state_dict = session.intake_state.model_dump(exclude_none=True)
        messages.append({
            "role": "system",
            "content": (
                f"Complete intake state:\n{json.dumps(state_dict, indent=2)}"
            ),
        })

        # Add safety flags if any
        if session.safety_flags:
            flags_text = "\n".join(
                f"- [{f.source}/{f.level.value}] {f.flag}" for f in session.safety_flags
            )
            messages.append({
                "role": "system",
                "content": f"Safety flags detected during intake:\n{flags_text}",
            })

        # Add deterministic rule triggers
        if session.audit_trace.deterministic_rules_triggered:
            messages.append({
                "role": "system",
                "content": (
                    f"CRITICAL: Deterministic safety rules were triggered: "
                    f"{', '.join(session.audit_trace.deterministic_rules_triggered)}. "
                    f"The disposition MUST be ER_NOW."
                ),
            })

        # Add conversation (full for finalize, up to 20)
        messages.extend([t.to_llm_message() for t in session.conversation[-20:]])

        return messages

    # ------------------------------------------------------------------ #
    # Stop condition logic
    # ------------------------------------------------------------------ #

    # Minimum number of *caller* turns (not including greeting) before
    # the system is allowed to finalize.  This prevents the LLM from
    # short-circuiting data collection after one or two answers.
    _MIN_TURNS_BEFORE_FINALIZE = 7

    # Minimum number of filled intake fields required before finalization.
    # This ensures the SBAR report has enough substance.  The field count
    # comes from StructuredIntakeState.filled_field_count() which checks
    # scalar fields for non-None and list fields for non-empty.
    _MIN_FILLED_FIELDS_BEFORE_FINALIZE = 6

    def _sbar_fields_complete(self, session: OrchestratorSession) -> bool:
        """Check whether the minimum SBAR-critical fields are populated.

        Required for finalization:
          - chief_complaint
          - onset_time
          - symptom_severity (not 'unknown')
          - filled_field_count >= _MIN_FILLED_FIELDS_BEFORE_FINALIZE
        """
        state = session.intake_state
        if not state.chief_complaint:
            return False
        if not state.onset_time:
            return False
        if not state.symptom_severity or state.symptom_severity == "unknown":
            return False
        if state.filled_field_count() < self._MIN_FILLED_FIELDS_BEFORE_FINALIZE:
            return False
        return True

    def _should_finalize(
        self,
        session: OrchestratorSession,
        intake_output: IntakeTurnOutput,
    ) -> bool:
        """Determine whether to stop questioning and finalize.

        Hard gates (must pass before any finalization):
        1. Minimum turn count (prevents premature cut-off)
        2. Required SBAR fields populated

        Then finalize if any of:
        - LLM confidence >= threshold
        - LLM explicitly signals readiness (finalize_ready)
        - Max turns reached (always finalizes — safety net)
        - No missing fields reported by LLM

        Args:
            session: Current session.
            intake_output: Latest intake turn output.

        Returns:
            True if should finalize.
        """
        # ── Hard gate: max turns always forces finalize (safety net) ──
        if session.turn_count >= session.max_turns:
            logger.info(f"[ORCH] Finalizing: max turns ({session.max_turns}) reached")
            return True

        # ── Hard gate: minimum turn count ─────────────────────────────
        if session.turn_count < self._MIN_TURNS_BEFORE_FINALIZE:
            logger.debug(
                f"[ORCH] Not finalizing: turn {session.turn_count} "
                f"< minimum {self._MIN_TURNS_BEFORE_FINALIZE}"
            )
            return False

        # ── Hard gate: required SBAR fields ───────────────────────────
        if not self._sbar_fields_complete(session):
            logger.debug(
                "[ORCH] Not finalizing: required SBAR fields still missing "
                f"(chief={bool(session.intake_state.chief_complaint)}, "
                f"onset={bool(session.intake_state.onset_time)}, "
                f"severity={session.intake_state.symptom_severity}, "
                f"history={session.intake_state.relevant_history is not None}, "
                f"meds={session.intake_state.meds is not None}, "
                f"allergies={session.intake_state.allergies is not None})"
            )
            return False

        # ── Soft signals (only after hard gates pass) ─────────────────
        # Confidence threshold
        if intake_output.confidence >= session.confidence_threshold:
            logger.info(
                f"[ORCH] Finalizing: confidence {intake_output.confidence:.2f} "
                f">= threshold {session.confidence_threshold}"
            )
            return True

        # LLM explicitly signals readiness
        if intake_output.finalize_ready:
            logger.info("[ORCH] Finalizing: LLM set finalize_ready=True")
            return True

        # No missing fields
        if not intake_output.missing_fields_prioritized:
            logger.info("[ORCH] Finalizing: no missing fields reported by LLM")
            return True

        return False

    # ------------------------------------------------------------------ #
    # Fallback helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _detect_followup_slot(follow_up_question: str) -> str | None:
        """Identify which SBAR slot a follow-up question targets.

        Returns the intake_state field name, or None if unclear.
        """
        for field, question in _LOW_CONFIDENCE_FOLLOWUP_QUESTIONS:
            if follow_up_question == question:
                return field
        # Also check loop-breaker wordings
        for field, question in _LOOP_BREAKER_QUESTIONS.items():
            if follow_up_question == question:
                return field
        return None

    def _get_fallback_intake(self, session: OrchestratorSession) -> IntakeTurnOutput:
        """Get a fallback IntakeTurnOutput when the LLM fails."""
        idx = min(session.turn_count - 1, len(FALLBACK_QUESTIONS) - 1)
        question = FALLBACK_QUESTIONS[idx]
        return safe_intake_turn_default(fallback_question=question)

    def _apply_extracted_fields(
        self,
        session: OrchestratorSession,
        intake_output: IntakeTurnOutput,
    ) -> None:
        """Apply extracted field updates to the session intake state.

        List fields are merged (append + case-insensitive dedupe + cap).
        Notes are concatenated with a separator and capped.
        Scalar fields use last-write-wins.
        """
        patch = intake_output.extracted_fields_update
        patch_data = patch.model_dump(exclude_none=True)
        if not patch_data:
            return

        state = session.intake_state
        for key, value in patch_data.items():
            try:
                if key in _LIST_FIELDS and isinstance(value, list):
                    current = getattr(state, key) or []
                    setattr(state, key, _merge_list(current, value))
                elif key == "notes" and isinstance(value, str):
                    existing = getattr(state, "notes") or ""
                    merged = f"{existing} | {value}".strip(" |") if existing else value
                    setattr(state, key, merged[:_MAX_NOTES_CHARS])
                else:
                    setattr(state, key, value)
            except (ValueError, TypeError) as e:
                logger.warning(f"[ORCH] Failed to set field {key}={value}: {e}")

    # ------------------------------------------------------------------ #
    # Phase 1 Helpers
    # ------------------------------------------------------------------ #

    def _build_phase1_messages(self, session: OrchestratorSession, protocol_snippets: list[ProtocolSnippet] | None = None) -> list[dict]:
        """Build messages for the Phase1TurnOutput LLM call."""
        messages: list[dict] = [
            {"role": "system", "content": PHASE1_TURN_SYSTEM_PROMPT},
        ]

        # Include current state context
        state = session.intake_state
        state_dict = state.model_dump(exclude_none=True, exclude_defaults=True)
        if state_dict:
            messages.append({
                "role": "system",
                "content": f"Current intake state:\n{json.dumps(state_dict, indent=2)}",
            })

        # Phase 2: Inject protocol context if available
        if protocol_snippets:
            messages.append({
                "role": "system",
                "content": self._format_protocol_context(protocol_snippets),
            })

        # Include last few conversation turns (last 6 for Phase1 — lean)
        messages.extend([t.to_llm_message() for t in session.conversation[-6:]])

        return messages

    @staticmethod
    def _format_protocol_context(snippets: list[ProtocolSnippet]) -> str:
        """Format protocol snippets as a system message for LLM context.

        The protocol context is clearly labelled as supplementary guidance
        that MUST NOT override safety rules or change urgency assessment.
        """
        lines = [
            "PROTOCOL CONTEXT (supplementary clinical guidance — "
            "does NOT override safety rules or red-flag decisions):",
        ]
        for s in snippets:
            lines.append(
                f"- [{s.id}] {s.title} (v{s.version}): {s.excerpt}"
            )
        lines.append(
            "Use this protocol context to inform your clinical questions "
            "and assessment. Do NOT use it to downgrade urgency."
        )
        return "\n".join(lines)

    @staticmethod
    def _is_unclear_answer(
        utterance: str,
        expected_answer_type: str | None = None,
    ) -> bool:
        """Return True if the caller utterance matches an unclear-answer signal.

        When expected_answer_type is 'yes_no', short yes/no variants are
        accepted and NOT treated as unclear.
        """
        if not utterance or not utterance.strip():
            return True

        # Part 3: If a yes/no answer is expected, accept recognised variants
        # but reject anything that isn't a valid yes/no response.
        if expected_answer_type == "yes_no":
            normalised = _normalize_yes_no(utterance)
            if normalised is not None:
                return False
            # Expected yes/no but answer is not a recognised variant → unclear
            return True

        for pat in _UNCLEAR_PATTERNS:
            if pat.fullmatch(utterance.strip()):
                return True
        return False

    def _append_trace(
        self,
        session: OrchestratorSession,
        user_text: str,
        confidence: float,
        disposition: str,
        escalation_required: bool,
        response: str,
        red_flags: list,
        rules: list,
        confidence_breakdown: Optional[ConfidenceBreakdown] = None,
        extracted_entities: Optional[dict] = None,
        protocol_hits: Optional[list[ProtocolHit]] = None,
        protocol_citations: Optional[list[str]] = None,
        # Phase 5: intake gate / transfer control fields
        intake_complete: bool = False,
        premature_transfer: bool = False,
        resistance_count: int = 0,
        transfer_reason: Optional[str] = None,
        override_applied: bool = False,
        # Bug 2: low-confidence continue-intake fields
        low_confidence_reprompt: bool = False,
        override_reason: Optional[str] = None,
    ) -> None:
        """Append one entry to the Phase 1 per-turn decision trace."""
        entry = DecisionTraceEntry(
            turn_number=session.turn_count,
            user_text=user_text,
            extracted_entities=extracted_entities or {},
            red_flags_triggered=list(red_flags),
            rules_triggered=list(rules),
            confidence_score=confidence,
            disposition=disposition,
            escalation_required=escalation_required,
            system_response=response,
            confidence_breakdown=confidence_breakdown,
            protocol_hits=protocol_hits or [],
            protocol_citations=protocol_citations or [],
            # intake gate fields
            intake_complete=intake_complete,
            premature_transfer=premature_transfer,
            resistance_count=resistance_count,
            transfer_reason=transfer_reason,
            override_applied=override_applied,
            # low-confidence continue-intake fields
            low_confidence_reprompt=low_confidence_reprompt,
            override_reason=override_reason,
        )
        session.decision_trace.append(entry)

        # Also store in audit trace for backward compatibility
        assert session.audit_trace is not None
        protocol_ids = [h.id for h in (protocol_hits or [])]
        session.audit_trace.add_entry(
            step="decision_trace",
            agent="phase1_engine",
            output_summary=(
                f"turn={session.turn_count}, "
                f"confidence={confidence:.2f}, "
                f"disposition={disposition}, "
                f"escalation={escalation_required}, "
                f"red_flags={red_flags}"
                + (f", protocols={protocol_ids}" if protocol_ids else "")
            ),
        )


# ---------------------------------------------------------------------------
# List-merge helpers
# ---------------------------------------------------------------------------

_LIST_FIELDS = frozenset({
    "red_flags_reported",
    "relevant_history",
    "meds",
    "allergies",
    "language_or_comms_barriers",
})

_MAX_LIST_ITEMS = 25
_MAX_NOTES_CHARS = 500


def _merge_list(existing: list[str], incoming: list[str]) -> list[str]:
    """Append *incoming* to *existing*, case-insensitive dedupe, preserve order, cap."""
    seen: set[str] = {item.lower() for item in existing}
    merged = list(existing)
    for item in incoming:
        stripped = item.strip()
        if not stripped:
            continue
        key = stripped.lower()
        if key not in seen:
            seen.add(key)
            merged.append(stripped)
    return merged[:_MAX_LIST_ITEMS]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _redact(text: str, max_len: int = 100) -> str:
    """Redact/truncate text for logging."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# Singleton
_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """Get or create the singleton Orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator
