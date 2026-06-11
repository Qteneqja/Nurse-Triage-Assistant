"""Spec-driven workflow interpreter (PR 3).

Runs any ``WorkflowSpec`` end-to-end on the platform: greeting and scripted
stages come straight from the spec, completion is decided by a NAMED rules
hook (default: required-fields check), records/summaries render from the
spec's templates and display fields, and the PR 2 conversation hooks
(narrative prefill, field-recorded, dynamic prompt) resolve from the hook
registry by name.

Vertical subclasses (Birchwood) may override the protected ``_run_*``
methods with their existing rich implementations — the scripted flow,
routing skeleton, and safety overlays are identical either way. Safety is
applied ABOVE this class by the WorkflowEngine and cannot be disabled from
a spec or a subclass.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from string import Formatter
from typing import Any

from src.orchestrator.schemas import OrchestratorSession
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
from src.platform.workflows.spec import (
    GenericAssessment,
    WorkflowSpec,
    resolve_hook,
)

logger = logging.getLogger(__name__)


class _SafeDict(dict):
    """Format-map dict that renders missing keys as 'unknown'."""

    def __missing__(self, key: str) -> str:  # pragma: no cover - trivial
        return "unknown"


def safe_format(template: str, values: dict[str, Any]) -> str:
    """Substitute {placeholders} without ever raising on missing keys."""
    try:
        return Formatter().vformat(template, (), _SafeDict(values))
    except Exception:  # malformed template — never crash a call
        logger.warning("Summary template failed to render", exc_info=True)
        return template


class SpecDrivenWorkflow(BaseWorkflow):
    """Generic BaseWorkflow implementation driven entirely by a spec."""

    def __init__(self, spec: WorkflowSpec) -> None:
        self.spec = spec
        # Resolve named hooks at construction — unknown names fail closed
        # at registration time, not mid-call.
        self._completion_rules = resolve_hook("completion_rules", spec.completion_rules)
        self._narrative_extractor = resolve_hook(
            "narrative_extractor", spec.narrative_extractor
        )
        self._field_recorded_hook = resolve_hook(
            "field_recorded_hook", spec.field_recorded_hook
        )
        self._dynamic_prompt_builder = resolve_hook(
            "dynamic_prompt_builder", spec.dynamic_prompt_builder
        )
        self._record_builder = resolve_hook("record_builder", spec.record_builder)

    # ------------------------------------------------------------------
    # BaseWorkflow contract
    # ------------------------------------------------------------------

    def get_spec(self) -> WorkflowSpec:
        return self.spec

    def get_definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            workflow_id=self.spec.workflow_id,
            vertical=self.spec.vertical,
            version=self.spec.version,
            display_name=self.spec.display_name,
            required_fields=list(self.spec.required_fields),
            supported_output_types=["GENERIC_INTAKE"],
            default_output_type="GENERIC_INTAKE",
            supports_post_call_extraction=bool(self.spec.extraction_entities),
            metadata={
                **self.spec.metadata,
                "spec_version": self.spec.spec_version,
                "dashboard_display_fields": list(self.spec.dashboard_display_fields),
                "safety_hooks": list(self.spec.safety_hooks),
            },
        )

    def get_scripted_intake_definition(self) -> ScriptedIntakeDefinition | None:
        if not self.spec.stages:
            return None
        return ScriptedIntakeDefinition(
            intro_text=self.spec.greeting,
            stages=[
                ScriptedStageDefinition(
                    stage_id=stage.stage_id,
                    field_name=stage.field_name,
                    prompt=stage.prompt,
                    field_type=stage.field_type,
                    expected_answer_type=stage.field_type,
                    required=stage.required,
                    reprompt_text=stage.reprompt_text,
                    allowed_values=stage.allowed_values,
                    max_attempts=stage.max_attempts,
                    sensitivity=stage.sensitivity,
                    hints=stage.hints,
                    speech_profile=stage.speech_profile,
                    timeout_seconds=stage.timeout_seconds,
                    speech_timeout=stage.speech_timeout,
                    multi_segment=stage.multi_segment,
                    dynamic_prompt=stage.dynamic_prompt,
                )
                for stage in self.spec.stages
            ],
        )

    def get_extraction_schema(self) -> dict[str, Any] | None:
        if not self.spec.extraction_entities:
            return None
        return {
            "schema_version": f"{self.spec.workflow_id}_extraction_v1",
            "entities": list(self.spec.extraction_entities),
        }

    def start_session(self, context: WorkflowContext) -> dict[str, Any]:
        session = OrchestratorSession(
            session_id=context.session_id,
            call_sid=context.call_sid,
        )
        self._apply_context(session, context)
        session.channel_metadata.setdefault(
            "scripted_intake", {"fields": {}, "completed": False}
        )
        return session.model_dump(mode="json")

    async def handle_turn(
        self,
        context: WorkflowContext,
        input: WorkflowInput,
    ) -> WorkflowTurnResult:
        session = self._load_session(context, input.session_state)
        final_result = self.build_final_result_from_session(
            context, session, dynamic_text=input.user_text
        )
        session.channel_metadata["workflow_final_result"] = final_result.model_dump(
            mode="json"
        )
        session.channel_metadata["stage"] = "FINAL"
        session.is_finalized = True
        session.finalization_reason = f"{self.spec.workflow_id}_complete"

        disposition = final_result.final_disposition
        record = final_result.structured_output.get("intake_record", {})
        return WorkflowTurnResult(
            assistant_text=self._final_message(disposition, final_result),
            stage="FINAL",
            should_continue=False,
            should_finalize=True,
            escalation_required=bool(record.get("transfer_department")),
            recommended_disposition=disposition,
            confidence_score=final_result.confidence_score,
            rules_triggered=list(final_result.rules_triggered),
            safety_events=list(final_result.safety_events),
            updated_state=session.model_dump(mode="json"),
            audit_metadata={
                "workflow_id": context.workflow_id,
                "deterministic": True,
                "spec_version": self.spec.spec_version,
                "finalization_reason": f"{self.spec.workflow_id}_complete",
            },
        )

    async def finalize(
        self,
        context: WorkflowContext,
        session_state: dict[str, Any],
    ) -> WorkflowFinalResult:
        session = self._load_session(context, session_state)
        return self.build_final_result_from_session(context, session)

    def build_final_result_from_session(
        self,
        context: WorkflowContext,
        session: OrchestratorSession,
        dynamic_text: str = "",
    ) -> WorkflowFinalResult:
        self._apply_context(session, context)
        fields = self._session_fields(session)
        assessment = self._run_completion_rules(fields, dynamic_text, session)
        record = self._build_record(context, session, fields, assessment)
        summaries = self._summaries(fields, assessment)
        record.setdefault("plain_summary", summaries["caller"])
        record.setdefault("shop_summary", summaries["business"])
        record.setdefault(
            "recommended_action",
            self.spec.recommended_actions.get(
                assessment.outcome, "Review the intake record and follow up."
            ),
        )
        return WorkflowFinalResult(
            final_disposition=assessment.outcome,
            confidence_score=assessment.confidence,
            summary=summaries["business"],
            structured_output={
                "output_type": "GENERIC_INTAKE",
                "intake_record": record,
                "dashboard_display_fields": list(self.spec.dashboard_display_fields),
            },
            safety_events=[
                {"rule_id": rule, "flag": (assessment.flags or [None])[0]}
                for rule in assessment.rules_triggered
            ],
            rules_triggered=list(assessment.rules_triggered),
            audit_metadata={
                "workflow_id": context.workflow_id,
                "deterministic": True,
                "spec_version": self.spec.spec_version,
                "missing_information": assessment.missing_information,
                "recommended_routing": assessment.outcome,
                "finalization_reason": f"{self.spec.workflow_id}_complete",
            },
        )

    # ------------------------------------------------------------------
    # PR 2 conversation hooks — resolved from the registry by name
    # ------------------------------------------------------------------

    def prefill_from_narrative(
        self,
        session: OrchestratorSession,
        stage: ScriptedStageDefinition,
        narrative: str,
    ) -> dict[str, Any]:
        if self._narrative_extractor is None:
            return {}
        return self._narrative_extractor(session, stage, narrative) or {}

    def on_field_recorded(
        self,
        session: OrchestratorSession,
        stage: ScriptedStageDefinition,
        value: Any,
    ) -> dict[str, Any]:
        if self._field_recorded_hook is None:
            return {}
        return self._field_recorded_hook(session, stage, value) or {}

    def build_dynamic_prompt(
        self,
        session: OrchestratorSession,
        stage: ScriptedStageDefinition,
    ) -> str | None:
        if self._dynamic_prompt_builder is None:
            return None
        return self._dynamic_prompt_builder(session, stage)

    # ------------------------------------------------------------------
    # Overridable internals (vertical subclasses plug in here)
    # ------------------------------------------------------------------

    def _run_completion_rules(
        self,
        fields: dict[str, Any],
        dynamic_text: str,
        session: OrchestratorSession,
    ) -> GenericAssessment:
        return self._completion_rules(fields, dynamic_text, self.spec)

    def _build_record(
        self,
        context: WorkflowContext,
        session: OrchestratorSession,
        fields: dict[str, Any],
        assessment: GenericAssessment,
    ) -> dict[str, Any]:
        if self._record_builder is not None:
            return self._record_builder(context, session, fields, assessment)
        display = {
            name: fields.get(name) for name in self.spec.dashboard_display_fields
        }
        return {
            "record_type": "generic_intake_v1",
            "intake_id": str(uuid.uuid4()),
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "workflow_id": context.workflow_id,
            "vertical": context.vertical,
            "fields": dict(fields),
            "dashboard": display,
            "outcome": assessment.outcome,
            "status": assessment.status,
            "reason": assessment.reason,
            "flags": list(assessment.flags),
            "missing_information": list(assessment.missing_information),
            "callback_needed": assessment.callback_needed,
            "human_review_required": assessment.human_review_required,
            "transfer_department": assessment.transfer_department,
            "confidence": assessment.confidence,
            "conversation_transcript": "\n".join(
                f"{turn.role}: {turn.text}"
                for turn in session.conversation
                if turn.text
            ),
        }

    def _final_message(
        self,
        disposition: str,
        final_result: WorkflowFinalResult,
    ) -> str:
        message = self.spec.final_messages.get(disposition)
        if message:
            return message
        if disposition.startswith("INCOMPLETE"):
            return self.spec.fallback_route.message
        record = final_result.structured_output.get("intake_record") or {}
        # plain_summary is the caller template already rendered with fields.
        return record.get("plain_summary") or self.spec.summary_templates.caller

    def _summaries(
        self,
        fields: dict[str, Any],
        assessment: GenericAssessment,
    ) -> dict[str, str]:
        values = {
            **fields,
            "outcome": assessment.outcome,
            "reason": assessment.reason,
            "missing": ", ".join(assessment.missing_information) or "none",
            "flags": ", ".join(assessment.flags) or "none",
        }
        return {
            "caller": safe_format(self.spec.summary_templates.caller, values),
            "business": safe_format(self.spec.summary_templates.business, values),
        }

    # ------------------------------------------------------------------
    # Shared plumbing
    # ------------------------------------------------------------------

    def _session_fields(self, session: OrchestratorSession) -> dict[str, Any]:
        scripted = session.channel_metadata.get("scripted_intake") or {}
        return dict(scripted.get("fields") or {})

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
        self._apply_context(session, context)
        return session

    def _apply_context(
        self,
        session: OrchestratorSession,
        context: WorkflowContext,
    ) -> None:
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
