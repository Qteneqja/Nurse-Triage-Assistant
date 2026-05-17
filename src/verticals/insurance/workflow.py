"""Insurance FNOL claims workflow."""

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
from src.verticals.insurance.constants import (
    INSURANCE_CLAIMS_FNOL_DISPOSITIONS,
    INSURANCE_CLAIMS_FNOL_OUTPUT_TYPE,
    INSURANCE_CLAIMS_FNOL_REQUIRED_FIELDS,
    INSURANCE_CLAIMS_FNOL_WORKFLOW_ID,
    INSURANCE_CLAIMS_FNOL_WORKFLOW_VERSION,
    INSURANCE_CLAIM_TYPE_VALUES,
    INSURANCE_VERTICAL,
)
from src.verticals.insurance.prompts import INSURANCE_FNOL_PROMPTS
from src.verticals.insurance.rules import (
    classify_insurance_claim,
    dynamic_follow_up_question,
    should_ask_dynamic_follow_up,
)
from src.verticals.insurance.schemas import (
    InsuranceClaimAssessment,
    InsuranceClaimIntake,
    InsuranceClaimRecord,
)


class InsuranceClaimsFnolWorkflow(BaseWorkflow):
    """Deterministic first notice of loss intake workflow."""

    def get_definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            workflow_id=INSURANCE_CLAIMS_FNOL_WORKFLOW_ID,
            vertical=INSURANCE_VERTICAL,
            version=INSURANCE_CLAIMS_FNOL_WORKFLOW_VERSION,
            display_name="Insurance FNOL Claims Intake",
            required_fields=list(INSURANCE_CLAIMS_FNOL_REQUIRED_FIELDS),
            supported_output_types=[INSURANCE_CLAIMS_FNOL_OUTPUT_TYPE],
            default_output_type=INSURANCE_CLAIMS_FNOL_OUTPUT_TYPE,
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
        session.channel_metadata.setdefault("insurance_claim", {})
        return session.model_dump(mode="json")

    async def handle_turn(
        self,
        context: WorkflowContext,
        input: WorkflowInput,
    ) -> WorkflowTurnResult:
        session = self._load_session(context, input.session_state)
        intake = _intake_from_session(session)

        if should_ask_dynamic_follow_up(intake, input.user_text):
            session.channel_metadata["stage"] = "DYNAMIC"
            session.channel_metadata.setdefault("insurance_claim", {})
            return WorkflowTurnResult(
                assistant_text=dynamic_follow_up_question(intake),
                stage="DYNAMIC",
                should_continue=True,
                should_finalize=False,
                escalation_required=False,
                recommended_disposition=None,
                confidence_score=None,
                updated_state=session.model_dump(mode="json"),
                audit_metadata={
                    "workflow_id": context.workflow_id,
                    "deterministic": True,
                    "follow_up_requested": True,
                },
            )

        assessment = classify_insurance_claim(
            intake,
            dynamic_text=input.user_text,
            existing_claim=session.channel_metadata.get("insurance_claim"),
        )
        final_result = self.build_final_result_from_session(
            context,
            session,
            assessment=assessment,
        )
        session.channel_metadata["workflow_final_result"] = final_result.model_dump(
            mode="json"
        )
        session.channel_metadata["stage"] = "FINAL"
        session.is_finalized = True
        session.finalization_reason = "insurance_fnol_complete"

        return WorkflowTurnResult(
            assistant_text=_spoken_final_message(assessment),
            stage="FINAL",
            should_continue=False,
            should_finalize=True,
            escalation_required=assessment.disposition == "EMERGENCY_SERVICES_NOW",
            recommended_disposition=assessment.disposition,
            confidence_score=assessment.confidence,
            rules_triggered=list(assessment.rules_triggered),
            safety_events=_safety_events(assessment),
            updated_state=session.model_dump(mode="json"),
            audit_metadata={
                "workflow_id": context.workflow_id,
                "deterministic": True,
                "output_type": INSURANCE_CLAIMS_FNOL_OUTPUT_TYPE,
                "finalization_reason": "insurance_fnol_complete",
                "insurance_missing_information": assessment.missing_information,
                "insurance_recommended_routing": assessment.disposition,
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
        assessment: InsuranceClaimAssessment | None = None,
    ) -> WorkflowFinalResult:
        self._apply_context(session, context)
        intake = _intake_from_session(session)
        if assessment is None:
            assessment = _assessment_from_session(session, intake)
        claim_record = _claim_record(context, intake, assessment)
        session.channel_metadata["insurance_claim"] = claim_record.model_dump(
            mode="json"
        )

        return WorkflowFinalResult(
            final_disposition=assessment.disposition,
            confidence_score=assessment.confidence,
            summary=_summary(claim_record),
            structured_output={
                "output_type": INSURANCE_CLAIMS_FNOL_OUTPUT_TYPE,
                "claim_record": claim_record.model_dump(mode="json"),
                "intake": intake.model_dump(mode="json"),
                "disposition_taxonomy": INSURANCE_CLAIMS_FNOL_DISPOSITIONS,
            },
            safety_events=_safety_events(assessment),
            rules_triggered=list(assessment.rules_triggered),
            audit_metadata={
                "workflow_id": context.workflow_id,
                "deterministic": True,
                "rules_engine": "insurance_claims_fnol_rules_v1",
                "finalization_reason": "insurance_fnol_complete",
                "insurance_missing_information": assessment.missing_information,
                "insurance_recommended_routing": assessment.disposition,
                "disclaimers_given": assessment.disclaimers_given,
            },
        )

    def get_extraction_schema(self) -> dict[str, Any] | None:
        return {
            "schema_version": "insurance_claims_fnol_extraction_v1",
            "entities": [
                "workflow_id",
                "vertical",
                "claim_type",
                "caller_name",
                "callback_number",
                "policy_number",
                "loss_datetime",
                "loss_location",
                "incident_summary",
                "emergency_or_safety_issue",
                "injuries_mentioned",
                "emergency_services_involved",
                "police_or_fire_report",
                "property_secure",
                "mitigation_needed",
                "documents_available",
                "missing_information",
                "recommended_routing",
                "confidence",
                "human_review_required",
                "disclaimers_given",
            ],
        }

    def get_scripted_intake_definition(self) -> ScriptedIntakeDefinition:
        return ScriptedIntakeDefinition(
            intro_text=(
                "Thanks for calling the insurance claims intake line. "
                "I can collect first notice of loss details, but coverage and "
                "claim decisions must be confirmed by a licensed broker or adjuster."
            ),
            stages=[
                ScriptedStageDefinition(
                    stage_id="CALLER_NAME",
                    field_name="caller_name",
                    prompt=INSURANCE_FNOL_PROMPTS["caller_name"],
                    field_type="text",
                    expected_answer_type="text",
                    max_attempts=3,
                    sensitivity="pii",
                ),
                ScriptedStageDefinition(
                    stage_id="CALLBACK_NUMBER",
                    field_name="callback_number",
                    prompt=INSURANCE_FNOL_PROMPTS["callback_number"],
                    field_type="phone",
                    expected_answer_type="phone",
                    sensitivity="pii",
                ),
                ScriptedStageDefinition(
                    stage_id="POLICY_NUMBER",
                    field_name="policy_number",
                    prompt=INSURANCE_FNOL_PROMPTS["policy_number"],
                    field_type="text",
                    expected_answer_type="text",
                    sensitivity="pii",
                ),
                ScriptedStageDefinition(
                    stage_id="CLAIM_TYPE",
                    field_name="claim_type",
                    prompt=INSURANCE_FNOL_PROMPTS["claim_type"],
                    field_type="enum",
                    expected_answer_type="choice",
                    allowed_values=list(INSURANCE_CLAIM_TYPE_VALUES),
                    hints=",".join(INSURANCE_CLAIM_TYPE_VALUES),
                ),
                ScriptedStageDefinition(
                    stage_id="LOSS_DATETIME",
                    field_name="loss_datetime",
                    prompt=INSURANCE_FNOL_PROMPTS["loss_datetime"],
                    field_type="text",
                    expected_answer_type="text",
                ),
                ScriptedStageDefinition(
                    stage_id="LOSS_LOCATION",
                    field_name="loss_location",
                    prompt=INSURANCE_FNOL_PROMPTS["loss_location"],
                    field_type="text",
                    expected_answer_type="text",
                    sensitivity="pii",
                ),
                ScriptedStageDefinition(
                    stage_id="INCIDENT_SUMMARY",
                    field_name="incident_summary",
                    prompt=INSURANCE_FNOL_PROMPTS["incident_summary"],
                    field_type="free_text",
                    expected_answer_type="free_text",
                    timeout_seconds=10,
                    speech_timeout="auto",
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
        self._apply_context(session, context)
        session.channel_metadata.setdefault("insurance_claim", {})
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


def _intake_from_session(session: OrchestratorSession) -> InsuranceClaimIntake:
    scripted = session.channel_metadata.get("scripted_intake") or {}
    fields = scripted.get("fields") or {}
    return InsuranceClaimIntake(
        caller_name=_clean(fields.get("caller_name")),
        callback_number=_clean(fields.get("callback_number")),
        policy_number=_clean(fields.get("policy_number")),
        claim_type=_clean(fields.get("claim_type")),
        loss_datetime=_clean(fields.get("loss_datetime")),
        loss_location=_clean(fields.get("loss_location")),
        incident_summary=_clean(fields.get("incident_summary")),
    )


def _assessment_from_session(
    session: OrchestratorSession,
    intake: InsuranceClaimIntake,
) -> InsuranceClaimAssessment:
    existing = session.channel_metadata.get("insurance_claim")
    if isinstance(existing, dict) and existing.get("recommended_routing"):
        return InsuranceClaimAssessment(
            disposition=existing.get("recommended_routing"),
            routing_reason=existing.get(
                "routing_reason",
                "Existing deterministic insurance claim routing was reused.",
            ),
            recommended_action=existing.get(
                "recommended_action",
                "Route according to the existing claim intake record.",
            ),
            relationship_to_policyholder=existing.get("relationship_to_policyholder"),
            preferred_callback_method=existing.get("preferred_callback_method")
            or "phone",
            emergency_or_safety_issue=bool(existing.get("emergency_or_safety_issue")),
            injuries_mentioned=bool(existing.get("injuries_mentioned")),
            emergency_services_involved=bool(
                existing.get("emergency_services_involved")
            ),
            police_or_fire_report=bool(existing.get("police_or_fire_report")),
            property_secure=existing.get("property_secure"),
            mitigation_needed=bool(existing.get("mitigation_needed")),
            documents_available=bool(existing.get("documents_available")),
            missing_information=list(existing.get("missing_information") or []),
            rules_triggered=list(existing.get("rules_triggered") or []),
            safety_flags=list(existing.get("safety_flags") or []),
            confidence=float(existing.get("confidence") or 0.7),
            human_review_required=bool(existing.get("human_review_required")),
            disclaimers_given=list(existing.get("disclaimers_given") or []),
        )
    return classify_insurance_claim(intake, existing_claim=existing)


def _claim_record(
    context: WorkflowContext,
    intake: InsuranceClaimIntake,
    assessment: InsuranceClaimAssessment,
) -> InsuranceClaimRecord:
    return InsuranceClaimRecord(
        workflow_id=context.workflow_id,
        vertical=context.vertical,
        caller_name=intake.caller_name,
        callback_number=intake.callback_number,
        policy_number=intake.policy_number,
        claim_type=intake.claim_type,
        loss_datetime=intake.loss_datetime,
        loss_location=intake.loss_location,
        incident_summary=intake.incident_summary,
        relationship_to_policyholder=assessment.relationship_to_policyholder,
        preferred_callback_method=assessment.preferred_callback_method,
        emergency_or_safety_issue=assessment.emergency_or_safety_issue,
        injuries_mentioned=assessment.injuries_mentioned,
        emergency_services_involved=assessment.emergency_services_involved,
        police_or_fire_report=assessment.police_or_fire_report,
        property_secure=assessment.property_secure,
        mitigation_needed=assessment.mitigation_needed,
        documents_available=assessment.documents_available,
        missing_information=assessment.missing_information,
        recommended_routing=assessment.disposition,
        confidence=assessment.confidence,
        human_review_required=assessment.human_review_required,
        disclaimers_given=assessment.disclaimers_given,
        routing_reason=assessment.routing_reason,
        recommended_action=assessment.recommended_action,
        rules_triggered=assessment.rules_triggered,
        safety_flags=assessment.safety_flags,
    )


def _summary(claim: InsuranceClaimRecord) -> str:
    return (
        f"{claim.recommended_routing} insurance FNOL for "
        f"{claim.claim_type or 'unknown claim type'} at "
        f"{claim.loss_location or 'unknown location'}."
    )


def _spoken_final_message(assessment: InsuranceClaimAssessment) -> str:
    if assessment.disposition == "EMERGENCY_SERVICES_NOW":
        return (
            "Thank you. Because you described an active safety issue, please contact "
            "emergency services now if you have not already. I have marked this "
            "claim for urgent follow-up by the claims team."
        )
    if assessment.disposition == "URGENT_ADJUSTER_REVIEW":
        return (
            "Thank you. I have marked this claim for urgent adjuster review. "
            "A licensed adjuster or broker can confirm coverage and next steps."
        )
    if assessment.disposition == "DOCUMENTS_NEEDED":
        return (
            "Thank you. I have recorded the claim details and marked that more "
            "information or documents are needed before review can continue."
        )
    if assessment.disposition == "INFORMATION_ONLY":
        return (
            "Thank you. I have recorded that you are looking for claims process "
            "information only. No coverage or approval decision has been made."
        )
    if assessment.disposition == "HUMAN_REVIEW":
        return (
            "Thank you. I have routed this claim intake for human review because "
            "some details need confirmation."
        )
    return (
        "Thank you. I have created a standard claim intake record. "
        "A licensed adjuster or broker can confirm coverage and next steps."
    )


def _safety_events(assessment: InsuranceClaimAssessment) -> list[dict[str, Any]]:
    return [
        {
            "rule_id": rule,
            "flag": assessment.safety_flags[0] if assessment.safety_flags else None,
        }
        for rule in assessment.rules_triggered
        if assessment.safety_flags or assessment.emergency_or_safety_issue
    ]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
