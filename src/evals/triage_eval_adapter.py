"""Offline adapter for running healthcare triage eval cases.

The adapter intentionally exercises the real healthcare orchestrator path while
using a deterministic structured-LLM stub. This keeps CI offline and makes the
healthcare safety gates, red-flag rules, and finalization logic the source of
truth for eval outcomes.
"""

from __future__ import annotations

import json
import re
from typing import Any, Type

from pydantic import BaseModel, Field

from src.llm.client import LLMCallError
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.schemas import (
    DispositionCategory,
    FinalizeOutput,
    IntakeStatePatch,
    IntakeTurnOutput,
    OrchestratorSession,
    Phase1Disposition,
    Phase1NextAction,
    Phase1TurnOutput,
    SBAR,
)
from src.verticals.healthcare.completeness import (
    MIN_DYNAMIC_TURNS_BEFORE_ROUTINE_FINALIZE,
    infer_healthcare_location_from_text,
)
from src.verticals.healthcare.constants import (
    HEALTHCARE_TRIAGE_VERSION,
    HEALTHCARE_TRIAGE_WORKFLOW_ID,
    HEALTHCARE_VERTICAL,
)


class TriageEvalCase(BaseModel):
    """Definition for one deterministic healthcare eval scenario."""

    case_id: str
    chief_complaint: str
    demographics: dict[str, Any] = Field(default_factory=dict)
    scripted_answers: list[str]
    expected: dict[str, Any] = Field(default_factory=dict)
    force_malformed_llm_output: bool = False
    initial_intake: dict[str, Any] = Field(default_factory=dict)


class EvalRunResult(BaseModel):
    """Normalized result returned by the eval adapter."""

    case_id: str
    transcript: list[dict[str, Any]]
    assistant_messages: list[str]
    is_finalized: bool
    final_disposition: str | None = None
    escalation_required: bool = False
    finalization_reason: str | None = None
    red_flags_triggered: list[str] = Field(default_factory=list)
    rules_triggered: list[str] = Field(default_factory=list)
    confidence_score: float | None = None
    sbar_fields: dict[str, str] = Field(default_factory=dict)
    sbar_available: bool = False
    decision_trace: list[dict[str, Any]] = Field(default_factory=list)
    audit_metadata: dict[str, Any] = Field(default_factory=dict)
    dynamic_turns_before_finalization: int | None = None
    healthcare_intake_completeness: dict[str, Any] | None = None
    healthcare_finalization_blocked_reason: str | None = None
    failed_closed: bool = False
    fail_reason: str | None = None
    raw_session: dict[str, Any] = Field(default_factory=dict)

    @property
    def emergency_red_flag_finalization(self) -> bool:
        """True when finalization was caused by a deterministic emergency gate."""

        return self.finalization_reason in {
            "critical_red_flag",
            "red_flag_score_threshold",
        }


class DeterministicEvalLLM:
    """Deterministic structured LLM stub used by offline evals.

    It deliberately emits high-confidence, finalize-ready intake signals once it
    has any useful data. If the orchestrator finalizes too early, the safety
    scorers catch that as a regression. If the orchestrator gates are intact,
    those soft signals are blocked until clinical completeness and minimum-turn
    requirements are satisfied.
    """

    def __init__(self, case: TriageEvalCase) -> None:
        self.case = case

    async def call(
        self,
        *,
        messages: list[dict[str, Any]],
        output_schema: Type[BaseModel],
        max_tokens: int = 500,
        temperature: float = 0.3,
        correlation_id: str | None = None,
    ) -> BaseModel:
        del max_tokens, temperature, correlation_id

        if output_schema is Phase1TurnOutput:
            if self.case.force_malformed_llm_output:
                raise LLMCallError("JSON validation failed after repair attempt")
            return self._phase1_output()

        if output_schema is IntakeTurnOutput:
            return self._intake_output(messages)

        if output_schema is FinalizeOutput:
            return self._finalize_output()

        raise LLMCallError(f"Unsupported eval schema: {output_schema!r}")

    def _phase1_output(self) -> Phase1TurnOutput:
        disposition = self.case.expected.get("phase1_disposition", "SELF_CARE")
        try:
            phase1_disposition = Phase1Disposition(disposition)
        except ValueError:
            phase1_disposition = Phase1Disposition.HUMAN_REVIEW

        return Phase1TurnOutput(
            confidence_score=float(self.case.expected.get("phase1_confidence", 0.95)),
            escalation_required=False,
            red_flags_triggered=[],
            rules_triggered=[],
            next_action=Phase1NextAction.ASK_QUESTION,
            disposition=phase1_disposition,
        )

    def _intake_output(self, messages: list[dict[str, Any]]) -> IntakeTurnOutput:
        latest_text = _latest_user_text(messages)
        state = _state_from_messages(messages)
        patch = _patch_for_text(latest_text, self.case.chief_complaint)
        merged_state = _merge_state(state, patch)
        missing = _missing_fields(merged_state, self.case.chief_complaint)
        next_question = _next_question(missing)

        return IntakeTurnOutput(
            extracted_fields_update=patch,
            missing_fields_prioritized=missing,
            next_question=next_question,
            confidence=0.95,
            finalize_ready=True,
            expected_answer_type="free_text",
        )

    def _finalize_output(self) -> FinalizeOutput:
        disposition = self.case.expected.get("final_disposition")
        if not disposition:
            disposition = (
                "HUMAN_REVIEW" if self.case.force_malformed_llm_output else "SELF_CARE"
            )
        try:
            category = DispositionCategory(disposition)
        except ValueError:
            category = DispositionCategory.HUMAN_REVIEW

        sbar = SBAR(
            situation=(
                f"Caller completed healthcare triage for {self.case.chief_complaint}."
            ),
            background=(
                "Offline eval case with deterministic scripted answers and "
                "no live LLM calls."
            ),
            assessment=(
                "Automated triage gathered intake fields and applied deterministic "
                "safety gates; no diagnosis was made."
            ),
            recommendation=(
                "Route according to final disposition and clinician review policy."
            ),
        )
        return FinalizeOutput(
            disposition=category,
            disposition_reasoning=(
                "Deterministic offline eval finalization after healthcare gates."
            ),
            safety_net_instructions=[
                "Seek urgent help if symptoms worsen or new red flags appear.",
            ],
            sbar=sbar,
            patient_summary=(
                "Thanks for sharing that information. A clinician can review "
                "the triage details and recommend the next step."
            ),
            llm_safety_flags=[],
        )


class TriageEvalAdapter:
    """Run scripted healthcare eval cases without Twilio or live LLM calls."""

    async def run_case(self, case: TriageEvalCase | dict[str, Any]) -> EvalRunResult:
        eval_case = (
            case
            if isinstance(case, TriageEvalCase)
            else TriageEvalCase.model_validate(case)
        )
        session = self._new_session(eval_case)
        orchestrator = Orchestrator(llm_client=DeterministicEvalLLM(eval_case))

        transcript: list[dict[str, Any]] = []
        last_result: dict[str, Any] | None = None

        for answer in eval_case.scripted_answers:
            if session.is_finalized:
                break
            result = await orchestrator.process_turn(session, answer)
            last_result = result
            transcript.append(
                {
                    "turn": session.turn_count,
                    "caller": answer,
                    "assistant": result.get("message", ""),
                    "action": result.get("action"),
                    "finalization_reason": session.finalization_reason,
                }
            )
            if result.get("action") in {"finalize", "escalate"}:
                break

        if session.is_finalized:
            await orchestrator.finalize(session)

        return _result_from_session(
            case_id=eval_case.case_id,
            session=session,
            transcript=transcript,
            last_result=last_result or {},
        )

    def _new_session(self, case: TriageEvalCase) -> OrchestratorSession:
        session = OrchestratorSession(
            session_id=case.case_id,
            call_sid=f"EVAL-{case.case_id}",
            vertical_key=HEALTHCARE_VERTICAL,
            workflow_id=HEALTHCARE_TRIAGE_WORKFLOW_ID,
            workflow_version=HEALTHCARE_TRIAGE_VERSION,
        )
        demographics = case.demographics
        session.intake_state.caller_name = str(demographics.get("name", "Eval Patient"))
        if demographics.get("age") is not None:
            session.intake_state.caller_age = int(demographics["age"])
        sex = demographics.get("sex")
        if sex:
            session.intake_state.caller_sex = str(sex).lower()
        session.intake_state.chief_complaint = case.chief_complaint
        for field_name, value in case.initial_intake.items():
            if hasattr(session.intake_state, field_name):
                setattr(session.intake_state, field_name, value)
        session.channel_metadata["stage"] = "DYNAMIC"
        session.channel_metadata["eval_case_id"] = case.case_id
        return session


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _state_from_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in reversed(messages):
        if message.get("role") != "system":
            continue
        content = str(message.get("content", ""))
        if "Current intake state" not in content:
            continue
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            continue
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _patch_for_text(text: str, chief_complaint: str) -> IntakeStatePatch:
    lowered = text.lower()
    patch_data: dict[str, Any] = {}

    if not chief_complaint and lowered:
        patch_data["chief_complaint"] = text[:120]

    onset_terms = (
        "started",
        "since",
        "today",
        "this morning",
        "yesterday",
        "days",
        "hours",
        "week",
    )
    if any(term in lowered for term in onset_terms):
        patch_data["onset_time"] = text[:120]

    if any(term in lowered for term in ("severe", "worst", "10 out of 10")):
        patch_data["symptom_severity"] = "severe"
    elif any(term in lowered for term in ("moderate", "getting worse", "worse", "5")):
        patch_data["symptom_severity"] = "moderate"
    elif any(term in lowered for term in ("mild", "2", "3", "low")):
        patch_data["symptom_severity"] = "mild"

    location = infer_healthcare_location_from_text(f"{chief_complaint} {text}")
    if location:
        patch_data["location"] = location

    associated_terms = (
        "no fever",
        "no shortness",
        "no chest pain",
        "no weakness",
        "no vision",
        "no other",
        "nausea",
        "vomiting",
        "diarrhea",
        "runny nose",
        "breathing is okay",
        "getting worse",
    )
    if any(term in lowered for term in associated_terms):
        patch_data["relevant_history"] = [text[:160]]

    if "not pregnant" in lowered:
        patch_data["pregnancy_status"] = "not_pregnant"
    elif "pregnant" in lowered:
        patch_data["pregnancy_status"] = "pregnant"

    if "no allergies" in lowered or "no known allergies" in lowered:
        patch_data["allergies"] = ["no known allergies"]
    elif "allergic" in lowered or "allergy" in lowered:
        patch_data["allergies"] = [text[:80]]

    meds = []
    for med in ("tylenol", "acetaminophen", "ibuprofen", "advil", "gravol"):
        if med in lowered:
            meds.append(med)
    if meds:
        patch_data["meds"] = meds

    return IntakeStatePatch(**patch_data)


def _merge_state(state: dict[str, Any], patch: IntakeStatePatch) -> dict[str, Any]:
    merged = dict(state)
    for key, value in patch.model_dump(exclude_none=True).items():
        if isinstance(value, list):
            existing = list(merged.get(key) or [])
            for item in value:
                if item not in existing:
                    existing.append(item)
            merged[key] = existing
        else:
            merged[key] = value
    return merged


def _missing_fields(state: dict[str, Any], chief_complaint: str) -> list[str]:
    missing: list[str] = []
    if not state.get("onset_time"):
        missing.append("onset_time")
    if not state.get("symptom_severity"):
        missing.append("symptom_severity")
    location_relevant = any(
        word in chief_complaint.lower()
        for word in ("ache", "injury", "pain", "rash", "swelling")
    )
    location_inferred = infer_healthcare_location_from_text(chief_complaint)
    if location_relevant and not (state.get("location") or location_inferred):
        missing.append("location")
    has_context = any(
        state.get(field)
        for field in (
            "relevant_history",
            "red_flags_reported",
            "meds",
            "allergies",
            "vitals_if_known",
            "pregnancy_status",
            "notes",
        )
    )
    if not has_context:
        missing.append("associated_symptoms_or_relevant_history")
    return missing


def _next_question(missing: list[str]) -> str:
    if "onset_time" in missing:
        return "When did this symptom first start?"
    if "symptom_severity" in missing:
        return "On a scale from mild to severe, how bad is it right now?"
    if "location" in missing:
        return "Where exactly are you feeling the symptom?"
    if "associated_symptoms_or_relevant_history" in missing:
        return "Do you have any other symptoms or relevant medical history?"
    return "Is there anything else important a clinician should know?"


def _result_from_session(
    *,
    case_id: str,
    session: OrchestratorSession,
    transcript: list[dict[str, Any]],
    last_result: dict[str, Any],
) -> EvalRunResult:
    trace = [entry.model_dump(mode="json") for entry in session.decision_trace]
    red_flags = _unique(
        flag for entry in session.decision_trace for flag in entry.red_flags_triggered
    )
    if session.audit_trace:
        red_flags = _unique(
            red_flags + session.audit_trace.deterministic_rules_triggered
        )
    rules = _unique(
        rule for entry in session.decision_trace for rule in entry.rules_triggered
    )
    assistant_messages = [
        item["assistant"] for item in transcript if item.get("assistant")
    ]
    assistant_messages.extend(
        turn.text for turn in session.conversation if turn.role == "assistant"
    )
    assistant_messages = _unique(assistant_messages)

    final_output = session.finalize_output
    sbar_fields: dict[str, str] = {}
    final_disposition: str | None = None
    if final_output is not None:
        final_disposition = final_output.disposition.value
        if final_output.sbar is not None:
            sbar_fields = final_output.sbar.model_dump()

    last_trace = session.decision_trace[-1] if session.decision_trace else None
    escalation_required = bool(
        (last_trace and last_trace.escalation_required)
        or final_disposition in {"ER_NOW", "URGENT", "HUMAN_REVIEW"}
    )
    completeness = session.channel_metadata.get("healthcare_intake_completeness")
    blocked_reason = session.channel_metadata.get(
        "healthcare_finalization_blocked_reason"
    )
    fail_reason = last_result.get("fail_reason")
    failed_closed = bool(
        session.finalization_reason
        in {
            "llm_validation_failure",
            "llm_timeout",
            "workflow_error",
            "post_check_safety_failure",
        }
        or fail_reason
    )
    dynamic_turns_before_finalization = (
        session.turn_count if session.is_finalized else None
    )

    audit_metadata = {
        "healthcare_intake_completeness": completeness,
        "healthcare_finalization_blocked_reason": blocked_reason,
        "finalization_reason": session.finalization_reason,
        "rules_triggered": rules,
        "red_flags_triggered": red_flags,
        "confidence_score": last_trace.confidence_score if last_trace else None,
        "escalation_required": escalation_required,
        "sbar_available": bool(
            all(sbar_fields.get(key) for key in _REQUIRED_SBAR_FIELDS)
        ),
    }

    return EvalRunResult(
        case_id=case_id,
        transcript=transcript,
        assistant_messages=assistant_messages,
        is_finalized=session.is_finalized,
        final_disposition=final_disposition,
        escalation_required=escalation_required,
        finalization_reason=session.finalization_reason,
        red_flags_triggered=red_flags,
        rules_triggered=rules,
        confidence_score=last_trace.confidence_score if last_trace else None,
        sbar_fields=sbar_fields,
        sbar_available=bool(all(sbar_fields.get(key) for key in _REQUIRED_SBAR_FIELDS)),
        decision_trace=trace,
        audit_metadata=audit_metadata,
        dynamic_turns_before_finalization=dynamic_turns_before_finalization,
        healthcare_intake_completeness=completeness,
        healthcare_finalization_blocked_reason=blocked_reason,
        failed_closed=failed_closed,
        fail_reason=fail_reason,
        raw_session=session.model_dump(mode="json"),
    )


_REQUIRED_SBAR_FIELDS = (
    "situation",
    "background",
    "assessment",
    "recommendation",
)


def _unique(values: Any) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


DEFAULT_MIN_DYNAMIC_TURNS = MIN_DYNAMIC_TURNS_BEFORE_ROUTINE_FINALIZE
