"""Healthcare triage workflow adapter.

This adapter keeps the existing clinical orchestrator intact while exposing it
through the platform workflow contract. The clinical safety hierarchy remains
inside src.orchestrator.orchestrator:

Rules > Protocol > LLM.
"""

from __future__ import annotations

from typing import Any

from src.orchestrator.orchestrator import Orchestrator, get_orchestrator
from src.orchestrator.schemas import (
    FinalizeOutput,
    OrchestratorSession,
)
from src.platform.workflows.base import BaseWorkflow
from src.platform.workflows.schemas import (
    ScriptedIntakeDefinition,
    ScriptedStageDefinition,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowFinalResult,
    WorkflowInput,
    WorkflowTurnResult,
)
from src.verticals.healthcare.constants import (
    HEALTHCARE_OUTPUT_TYPES,
    HEALTHCARE_REQUIRED_FIELDS,
    HEALTHCARE_TRIAGE_DISPLAY_NAME,
    HEALTHCARE_TRIAGE_VERSION,
    HEALTHCARE_TRIAGE_WORKFLOW_ID,
    HEALTHCARE_VERTICAL,
)
from src.verticals.healthcare.schemas import HealthcareExtractionSchema


class HealthcareTriageWorkflow(BaseWorkflow):
    """Platform workflow wrapper around the existing healthcare orchestrator."""

    def __init__(self, orchestrator: Orchestrator | None = None) -> None:
        self._orchestrator = orchestrator

    def get_definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            workflow_id=HEALTHCARE_TRIAGE_WORKFLOW_ID,
            vertical=HEALTHCARE_VERTICAL,
            version=HEALTHCARE_TRIAGE_VERSION,
            display_name=HEALTHCARE_TRIAGE_DISPLAY_NAME,
            required_fields=HEALTHCARE_REQUIRED_FIELDS,
            supported_output_types=HEALTHCARE_OUTPUT_TYPES,
            default_output_type="SBAR",
            supports_post_call_extraction=True,
        )

    def start_session(self, context: WorkflowContext) -> dict[str, Any]:
        session = OrchestratorSession(
            session_id=context.session_id,
            call_sid=context.call_sid,
            organization_id=context.organization_id,
            vertical_key=context.vertical,
            workflow_id=context.workflow_id,
            workflow_version=context.workflow_version,
            phone_number_id=context.phone_number_id,
        )
        session.channel_metadata.update(context.metadata)
        return session.model_dump(mode="json")

    async def handle_turn(
        self,
        context: WorkflowContext,
        input: WorkflowInput,
    ) -> WorkflowTurnResult:
        session = self._load_session(context, input.session_state)
        result = await self._get_orchestrator().process_turn(session, input.user_text)
        action = result.get("action", "ask")

        latest_trace = session.decision_trace[-1] if session.decision_trace else None
        phase1_output = result.get("phase1_output")

        recommended_disposition = None
        if phase1_output is not None and hasattr(phase1_output, "disposition"):
            recommended_disposition = phase1_output.disposition.value
        elif latest_trace is not None:
            recommended_disposition = latest_trace.disposition
        elif session.finalize_output is not None:
            recommended_disposition = session.finalize_output.disposition.value

        confidence_score = (
            latest_trace.confidence_score if latest_trace is not None else None
        )
        trace_escalation_required = bool(
            latest_trace.escalation_required if latest_trace else False
        )
        workflow_escalation_required = action == "escalate"
        finalization_reason = (
            session.finalization_reason
            or session.channel_metadata.get("finalization_reason")
            or result.get("finalization_reason")
        )

        rules_triggered = []
        if latest_trace is not None:
            rules_triggered.extend(latest_trace.rules_triggered)
        if session.audit_trace is not None:
            rules_triggered.extend(session.audit_trace.deterministic_rules_triggered)
        rules_triggered = _dedupe(rules_triggered)

        return WorkflowTurnResult(
            assistant_text=result.get("message", ""),
            stage=session.channel_metadata.get("stage", "DYNAMIC"),
            should_continue=action == "ask",
            should_finalize=action == "finalize",
            escalation_required=workflow_escalation_required,
            recommended_disposition=recommended_disposition,
            confidence_score=confidence_score,
            rules_triggered=rules_triggered,
            safety_events=[
                flag.model_dump(mode="json") for flag in session.safety_flags
            ],
            updated_state=session.model_dump(mode="json"),
            audit_metadata={
                "legacy_action": action,
                "fail_reason": result.get("fail_reason"),
                "low_confidence_reprompt": result.get("low_confidence_reprompt", False),
                "override_applied": result.get("override_applied", False),
                "trace_escalation_required": trace_escalation_required,
                "trace_escalation_suppressed": (
                    action == "ask" and trace_escalation_required
                ),
                "finalization_reason": finalization_reason,
                "healthcare_intake_completeness": session.channel_metadata.get(
                    "healthcare_intake_completeness"
                ),
                "healthcare_finalization_blocked_reason": (
                    session.channel_metadata.get(
                        "healthcare_finalization_blocked_reason"
                    )
                ),
            },
        )

    async def finalize(
        self,
        context: WorkflowContext,
        session_state: dict[str, Any],
    ) -> WorkflowFinalResult:
        session = self._load_session(context, session_state)
        finalize_output = await self._get_orchestrator().finalize(
            session,
            correlation_id=context.correlation_id,
        )
        return self.build_final_result_from_session(context, session, finalize_output)

    def build_final_result_from_session(
        self,
        context: WorkflowContext,
        session: OrchestratorSession,
        finalize_output: FinalizeOutput | None = None,
    ) -> WorkflowFinalResult:
        finalize_output = finalize_output or session.finalize_output
        if finalize_output is None:
            return WorkflowFinalResult(
                final_disposition="HUMAN_REVIEW",
                confidence_score=_latest_confidence(session) or 0.0,
                summary="Final healthcare triage result was unavailable.",
                structured_output={
                    "output_type": "SBAR",
                    "sbar_available": False,
                    "intake_state": session.intake_state.model_dump(exclude_none=True),
                },
                safety_events=[
                    flag.model_dump(mode="json") for flag in session.safety_flags
                ],
                rules_triggered=_rules_from_session(session),
                audit_metadata={
                    "workflow_id": context.workflow_id,
                    "protocols_used": _protocols_from_session(session),
                    "finalization_reason": (
                        session.finalization_reason or "missing_session_state"
                    ),
                },
            )

        return WorkflowFinalResult(
            final_disposition=finalize_output.disposition.value,
            confidence_score=_latest_confidence(session) or 0.8,
            summary=finalize_output.patient_summary,
            structured_output={
                "output_type": "SBAR",
                "finalization_reason": session.finalization_reason,
                "disposition_reasoning": finalize_output.disposition_reasoning,
                "safety_net_instructions": finalize_output.safety_net_instructions,
                "sbar_report": finalize_output.sbar_report,
                "sbar": finalize_output.sbar.model_dump()
                if finalize_output.sbar
                else None,
                "intake_state": session.intake_state.model_dump(exclude_none=True),
            },
            safety_events=[
                flag.model_dump(mode="json") for flag in session.safety_flags
            ],
            rules_triggered=_rules_from_session(session),
            audit_metadata={
                "workflow_id": context.workflow_id,
                "audit_entries": len(session.audit_trace.entries)
                if session.audit_trace
                else 0,
                "protocols_used": _protocols_from_session(session),
                "finalization_reason": session.finalization_reason,
            },
        )

    def get_extraction_schema(self) -> dict[str, Any] | None:
        return HealthcareExtractionSchema().model_dump()

    def get_scripted_intake_definition(self) -> ScriptedIntakeDefinition:
        return ScriptedIntakeDefinition(
            intro_text=(
                "Hello, this is Astra, a clinical triage assistant. "
                "This is a decision-support tool, not a diagnostic service. "
                "If you are experiencing a life-threatening emergency, "
                "please hang up and call 9 1 1 immediately. "
            ),
            stages=[
                ScriptedStageDefinition(
                    stage_id="NAME",
                    field_name="caller_name",
                    prompt=(
                        "I will be asking you a series of questions to help assess "
                        "your symptoms. Can I start with your full name?"
                    ),
                    expected_answer_type="text",
                    field_type="text",
                    max_attempts=3,
                    sensitivity="phi",
                    reprompt_text=(
                        "I'm sorry, I didn't quite catch your name. "
                        "Could you please say just your first and last name?"
                    ),
                ),
                ScriptedStageDefinition(
                    stage_id="AGE",
                    field_name="caller_age",
                    prompt="Thank you. What is your age?",
                    expected_answer_type="number",
                    field_type="integer",
                    sensitivity="phi",
                ),
                ScriptedStageDefinition(
                    stage_id="SEX",
                    field_name="caller_sex",
                    prompt=(
                        "And what is your biological sex? Please say male, female, "
                        "or prefer not to say."
                    ),
                    expected_answer_type="choice",
                    field_type="enum",
                    allowed_values=["male", "female", "prefer not to say"],
                    hints="male,female,prefer not to say",
                    sensitivity="phi",
                ),
                ScriptedStageDefinition(
                    stage_id="CHIEF_COMPLAINT",
                    field_name="chief_complaint",
                    prompt=(
                        "Thank you. Now, what brings you in today? Please describe "
                        "your main symptom or concern."
                    ),
                    expected_answer_type="free_text",
                    field_type="free_text",
                    speech_timeout="auto",
                    timeout_seconds=10,
                    sensitivity="phi",
                ),
            ],
        )

    def _load_session(
        self,
        context: WorkflowContext,
        session_state: dict[str, Any],
    ) -> OrchestratorSession:
        if session_state:
            session = OrchestratorSession.model_validate(session_state)
        else:
            session = OrchestratorSession(
                session_id=context.session_id,
                call_sid=context.call_sid,
            )

        session.organization_id = context.organization_id
        session.vertical_key = context.vertical
        session.workflow_id = context.workflow_id
        session.workflow_version = context.workflow_version
        session.phone_number_id = context.phone_number_id
        session.channel_metadata.setdefault("workflow_id", context.workflow_id)
        session.channel_metadata.setdefault("vertical_key", context.vertical)
        session.channel_metadata.setdefault(
            "workflow_version", context.workflow_version
        )
        if context.organization_id:
            session.channel_metadata.setdefault(
                "organization_id", context.organization_id
            )
        if context.phone_number_id:
            session.channel_metadata.setdefault(
                "phone_number_id", context.phone_number_id
            )
        return session

    def _get_orchestrator(self) -> Orchestrator:
        return self._orchestrator or get_orchestrator()


def _latest_confidence(session: OrchestratorSession) -> float | None:
    if session.decision_trace:
        return session.decision_trace[-1].confidence_score
    return None


def _rules_from_session(session: OrchestratorSession) -> list[str]:
    rules: list[str] = []
    for entry in session.decision_trace:
        rules.extend(entry.rules_triggered)
    if session.audit_trace is not None:
        rules.extend(session.audit_trace.deterministic_rules_triggered)
    return _dedupe(rules)


def _protocols_from_session(session: OrchestratorSession) -> list[str]:
    protocols: list[str] = []
    for entry in session.decision_trace:
        protocols.extend(entry.protocol_citations)
        protocols.extend([hit.id for hit in entry.protocol_hits])
    return _dedupe(protocols)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
