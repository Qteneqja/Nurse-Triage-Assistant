"""Property management maintenance workflow."""

from __future__ import annotations

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
from src.verticals.property_management.constants import (
    PROPERTY_MAINTENANCE_DISPOSITIONS,
    PROPERTY_MAINTENANCE_OUTPUT_TYPE,
    PROPERTY_MAINTENANCE_REQUIRED_FIELDS,
    PROPERTY_MAINTENANCE_WORKFLOW_ID,
    PROPERTY_MAINTENANCE_WORKFLOW_VERSION,
    PROPERTY_MANAGEMENT_VERTICAL,
)
from src.verticals.property_management.prompts import PROPERTY_MAINTENANCE_PROMPTS
from src.verticals.property_management.rules import classify_maintenance_request
from src.verticals.property_management.schemas import (
    MaintenanceIntake,
    MaintenanceWorkOrder,
)


class PropertyManagementMaintenanceWorkflow(BaseWorkflow):
    """Deterministic tenant maintenance intake workflow."""

    def get_definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            workflow_id=PROPERTY_MAINTENANCE_WORKFLOW_ID,
            vertical=PROPERTY_MANAGEMENT_VERTICAL,
            version=PROPERTY_MAINTENANCE_WORKFLOW_VERSION,
            display_name="Property Management Maintenance Intake",
            required_fields=list(PROPERTY_MAINTENANCE_REQUIRED_FIELDS),
            supported_output_types=[PROPERTY_MAINTENANCE_OUTPUT_TYPE],
            default_output_type=PROPERTY_MAINTENANCE_OUTPUT_TYPE,
            supports_post_call_extraction=True,
        )

    def start_session(self, context: WorkflowContext) -> dict[str, Any]:
        session = OrchestratorSession(
            session_id=context.session_id,
            call_sid=context.call_sid,
        )
        self._apply_context(session, context)
        session.channel_metadata.setdefault(
            "scripted_intake",
            {"fields": {}, "completed": False},
        )
        return session.model_dump(mode="json")

    async def handle_turn(
        self,
        context: WorkflowContext,
        input: WorkflowInput,
    ) -> WorkflowTurnResult:
        session = self._load_session(context, input.session_state)
        final_result = self.build_final_result_from_session(context, session)
        session.channel_metadata["workflow_final_result"] = final_result.model_dump(
            mode="json"
        )
        session.channel_metadata["stage"] = "FINAL"

        disposition = final_result.final_disposition
        return WorkflowTurnResult(
            assistant_text=_spoken_final_message(disposition, final_result),
            stage="FINAL",
            should_continue=False,
            should_finalize=True,
            escalation_required=disposition == "EMERGENCY",
            recommended_disposition=disposition,
            confidence_score=final_result.confidence_score,
            rules_triggered=list(final_result.rules_triggered),
            safety_events=list(final_result.safety_events),
            updated_state=session.model_dump(mode="json"),
            audit_metadata={
                "workflow_id": context.workflow_id,
                "deterministic": True,
                "output_type": PROPERTY_MAINTENANCE_OUTPUT_TYPE,
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
    ) -> WorkflowFinalResult:
        self._apply_context(session, context)
        intake = _intake_from_session(session)
        classification = classify_maintenance_request(intake)
        work_order = MaintenanceWorkOrder(
            caller_name=intake.caller_name,
            property_address=intake.property_address,
            unit_number=intake.unit_number,
            issue_type=intake.issue_type,
            issue_description=intake.issue_description,
            access_permission=intake.access_permission,
            callback_phone=intake.callback_phone,
            disposition=classification.disposition,
            urgency_reason=classification.urgency_reason,
            recommended_action=classification.recommended_action,
            vendor_type=classification.vendor_type,
            safety_flags=classification.safety_flags,
            confidence_score=classification.confidence_score,
        )

        safety_events = [
            {
                "rule_id": rule,
                "flag": classification.safety_flags[0]
                if classification.safety_flags
                else None,
            }
            for rule in classification.rules_triggered
        ]
        return WorkflowFinalResult(
            final_disposition=classification.disposition,
            confidence_score=classification.confidence_score,
            summary=_summary(work_order),
            structured_output={
                "output_type": PROPERTY_MAINTENANCE_OUTPUT_TYPE,
                "work_order": work_order.model_dump(mode="json"),
                "intake": intake.model_dump(mode="json"),
                "disposition_taxonomy": PROPERTY_MAINTENANCE_DISPOSITIONS,
            },
            safety_events=safety_events,
            rules_triggered=classification.rules_triggered,
            audit_metadata={
                "workflow_id": context.workflow_id,
                "deterministic": True,
                "rules_engine": "property_maintenance_rules_v1",
            },
        )

    def get_extraction_schema(self) -> dict[str, Any] | None:
        return {
            "schema_version": "property_maintenance_extraction_v1",
            "entities": [
                "issue_type",
                "urgency",
                "property_address",
                "unit_number",
                "access_permission",
                "vendor_type",
                "emergency_flags",
                "recommended_action",
                "tenant_sentiment",
                "repeat_issue",
            ],
        }

    def get_scripted_intake_definition(self) -> ScriptedIntakeDefinition:
        return ScriptedIntakeDefinition(
            stages=[
                ScriptedStageDefinition(
                    stage_id="CALLER_NAME",
                    field_name="caller_name",
                    prompt=PROPERTY_MAINTENANCE_PROMPTS["caller_name"],
                    field_type="text",
                    expected_answer_type="text",
                    max_attempts=3,
                    sensitivity="pii",
                    reprompt_text=(
                        "I'm sorry, I didn't quite catch your name. "
                        "Could you please say just your first and last name?"
                    ),
                ),
                ScriptedStageDefinition(
                    stage_id="PROPERTY_ADDRESS",
                    field_name="property_address",
                    prompt=PROPERTY_MAINTENANCE_PROMPTS["property_address"],
                    field_type="text",
                    expected_answer_type="text",
                    sensitivity="pii",
                ),
                ScriptedStageDefinition(
                    stage_id="UNIT_NUMBER",
                    field_name="unit_number",
                    prompt=PROPERTY_MAINTENANCE_PROMPTS["unit_number"],
                    field_type="text",
                    expected_answer_type="text",
                    sensitivity="pii",
                ),
                ScriptedStageDefinition(
                    stage_id="ISSUE_TYPE",
                    field_name="issue_type",
                    prompt=PROPERTY_MAINTENANCE_PROMPTS["issue_type"],
                    field_type="enum",
                    expected_answer_type="choice",
                    allowed_values=[
                        "plumbing",
                        "heat",
                        "electrical",
                        "lockout",
                        "appliance",
                        "noise",
                        "other",
                    ],
                    hints="plumbing,heat,electrical,lockout,appliance,noise,other",
                ),
                ScriptedStageDefinition(
                    stage_id="ISSUE_DESCRIPTION",
                    field_name="issue_description",
                    prompt=PROPERTY_MAINTENANCE_PROMPTS["issue_description"],
                    field_type="free_text",
                    expected_answer_type="free_text",
                    timeout_seconds=10,
                    speech_timeout="auto",
                ),
                ScriptedStageDefinition(
                    stage_id="ACCESS_PERMISSION",
                    field_name="access_permission",
                    prompt=PROPERTY_MAINTENANCE_PROMPTS["access_permission"],
                    field_type="enum",
                    expected_answer_type="choice",
                    allowed_values=["yes", "no"],
                    hints="yes,no",
                ),
                ScriptedStageDefinition(
                    stage_id="CALLBACK_PHONE",
                    field_name="callback_phone",
                    prompt=PROPERTY_MAINTENANCE_PROMPTS["callback_phone"],
                    field_type="phone",
                    expected_answer_type="phone",
                    sensitivity="pii",
                ),
            ]
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


def _intake_from_session(session: OrchestratorSession) -> MaintenanceIntake:
    scripted = session.channel_metadata.get("scripted_intake") or {}
    fields = scripted.get("fields") or {}
    return MaintenanceIntake(
        caller_name=_clean(fields.get("caller_name")),
        property_address=_clean(fields.get("property_address")),
        unit_number=_clean(fields.get("unit_number")),
        issue_type=_clean(fields.get("issue_type")),
        issue_description=_clean(fields.get("issue_description")),
        access_permission=_clean(fields.get("access_permission")),
        callback_phone=_clean(fields.get("callback_phone")),
    )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _summary(work_order: MaintenanceWorkOrder) -> str:
    location = " ".join(
        part for part in [work_order.property_address, work_order.unit_number] if part
    )
    issue = work_order.issue_description or work_order.issue_type or "maintenance issue"
    return (
        f"{work_order.disposition} maintenance request for {location or 'unknown unit'}: "
        f"{issue}."
    )


def _spoken_final_message(
    disposition: str,
    final_result: WorkflowFinalResult,
) -> str:
    action = final_result.structured_output.get("work_order", {}).get(
        "recommended_action",
        "A property manager will review this request.",
    )
    if disposition == "EMERGENCY":
        return (
            "Thank you. I have marked this as an emergency maintenance request. "
            f"{action}"
        )
    if disposition == "SAME_DAY":
        return (
            f"Thank you. I have marked this as a same-day maintenance request. {action}"
        )
    if disposition == "SCHEDULED_REPAIR":
        return (
            f"Thank you. I have created a standard maintenance repair request. {action}"
        )
    if disposition == "INFORMATION_ONLY":
        return "Thank you. I have recorded your request for property office follow-up."
    return (
        "Thank you. I have recorded your request for human review, and a property "
        "manager should follow up."
    )
