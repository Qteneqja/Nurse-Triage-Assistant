"""ORCA automotive collision intake workflow customized for Birchwood demo."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import src.config as config
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
from src.verticals.automotive_collision.constants import (
    AUTOMOTIVE_COLLISION_VERTICAL,
    BIRCHWOOD_COLLISION_CLIENT_TARGET,
    BIRCHWOOD_COLLISION_DISPLAY_NAME,
    BIRCHWOOD_COLLISION_OUTCOMES,
    BIRCHWOOD_COLLISION_OUTPUT_TYPE,
    BIRCHWOOD_COLLISION_POWERED_BY,
    BIRCHWOOD_COLLISION_REQUIRED_FIELDS,
    BIRCHWOOD_COLLISION_STATUS,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
    BIRCHWOOD_COLLISION_WORKFLOW_VERSION,
)
from src.verticals.automotive_collision.prompts import (
    BIRCHWOOD_COLLISION_INTRO,
    BIRCHWOOD_COLLISION_PROMPTS,
)
from src.verticals.automotive_collision.rules import (
    classify_collision_intake,
    parse_intake_bool,
    parse_vehicle_year,
)
from src.verticals.automotive_collision.schemas import (
    AutomotiveCollisionAssessment,
    AutomotiveCollisionIntake,
    AutomotiveCollisionRecord,
)


class BirchwoodCollisionIntakeWorkflow(BaseWorkflow):
    """Deterministic Birchwood-targeted collision intake workflow powered by ORCA."""

    def get_definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
            vertical=AUTOMOTIVE_COLLISION_VERTICAL,
            version=BIRCHWOOD_COLLISION_WORKFLOW_VERSION,
            display_name=BIRCHWOOD_COLLISION_DISPLAY_NAME,
            required_fields=list(BIRCHWOOD_COLLISION_REQUIRED_FIELDS),
            supported_output_types=[BIRCHWOOD_COLLISION_OUTPUT_TYPE],
            default_output_type=BIRCHWOOD_COLLISION_OUTPUT_TYPE,
            supports_post_call_extraction=True,
            metadata={
                "powered_by": BIRCHWOOD_COLLISION_POWERED_BY,
                "client_target": BIRCHWOOD_COLLISION_CLIENT_TARGET,
                "status": BIRCHWOOD_COLLISION_STATUS,
            },
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
        session.channel_metadata.setdefault("automotive_collision", {})
        return session.model_dump(mode="json")

    async def handle_turn(
        self,
        context: WorkflowContext,
        input: WorkflowInput,
    ) -> WorkflowTurnResult:
        session = self._load_session(context, input.session_state)
        final_result = self.build_final_result_from_session(
            context,
            session,
            dynamic_text=input.user_text,
        )
        session.channel_metadata["workflow_final_result"] = final_result.model_dump(
            mode="json"
        )
        session.channel_metadata["stage"] = "FINAL"
        session.is_finalized = True
        session.finalization_reason = "automotive_collision_intake_complete"

        disposition = final_result.final_disposition
        return WorkflowTurnResult(
            assistant_text=_spoken_final_message(disposition, final_result),
            stage="FINAL",
            should_continue=False,
            should_finalize=True,
            escalation_required=disposition
            in {"TRANSFER_COLLISION_CENTER", "TRANSFER_GLASS_DEPARTMENT"},
            recommended_disposition=disposition,
            confidence_score=final_result.confidence_score,
            rules_triggered=list(final_result.rules_triggered),
            safety_events=list(final_result.safety_events),
            updated_state=session.model_dump(mode="json"),
            audit_metadata={
                "workflow_id": context.workflow_id,
                "deterministic": True,
                "output_type": BIRCHWOOD_COLLISION_OUTPUT_TYPE,
                "powered_by": BIRCHWOOD_COLLISION_POWERED_BY,
                "client_target": BIRCHWOOD_COLLISION_CLIENT_TARGET,
                "status": BIRCHWOOD_COLLISION_STATUS,
                "automotive_collision_missing_information": final_result.audit_metadata.get(
                    "automotive_collision_missing_information",
                    [],
                ),
                "automotive_collision_recommended_routing": disposition,
                "finalization_reason": "automotive_collision_intake_complete",
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
        intake = _intake_from_session(session)
        assessment = classify_collision_intake(intake, dynamic_text=dynamic_text)
        record = _intake_record(context, session, intake, assessment)
        session.channel_metadata["automotive_collision"] = record.model_dump(
            mode="json"
        )

        safety_events = [
            {"rule_id": rule, "flag": _first_flag_for_rule(assessment.flags)}
            for rule in assessment.rules_triggered
        ]
        return WorkflowFinalResult(
            final_disposition=assessment.outcome,
            confidence_score=assessment.confidence,
            summary=_summary(record),
            structured_output={
                "output_type": BIRCHWOOD_COLLISION_OUTPUT_TYPE,
                "intake_record": record.model_dump(mode="json"),
                "intake": intake.model_dump(mode="json"),
                "disposition_taxonomy": BIRCHWOOD_COLLISION_OUTCOMES,
            },
            safety_events=safety_events,
            rules_triggered=list(assessment.rules_triggered),
            audit_metadata={
                "workflow_id": context.workflow_id,
                "deterministic": True,
                "rules_engine": "automotive_collision_birchwood_rules_v1",
                "powered_by": BIRCHWOOD_COLLISION_POWERED_BY,
                "client_target": BIRCHWOOD_COLLISION_CLIENT_TARGET,
                "status": BIRCHWOOD_COLLISION_STATUS,
                "finalization_reason": "automotive_collision_intake_complete",
                "automotive_collision_missing_information": (
                    assessment.missing_information
                ),
                "automotive_collision_recommended_routing": assessment.outcome,
                "disclaimers_given": assessment.disclaimers_given,
            },
        )

    def get_extraction_schema(self) -> dict[str, Any] | None:
        return {
            "schema_version": "automotive_collision_birchwood_extraction_v1",
            "entities": [
                "workflow_id",
                "vertical",
                "powered_by",
                "client_target",
                "status",
                "caller_name",
                "phone",
                "email",
                "address",
                "vehicle_year",
                "vehicle_make",
                "vehicle_model",
                "license_plate",
                "is_drivable",
                "damage_type",
                "glass_only",
                "body_damage",
                "incident_description",
                "filing_insurance_claim",
                "claim_number",
                "private_pay",
                "preferred_collision_center",
                "is_luxury",
                "is_vw",
                "is_rebuilt_or_salvage",
                "flags",
                "missing_information",
                "recommended_routing",
                "transfer_department",
                "decline_reason",
                "callback_needed",
                "transcript_summary",
                "disclaimers_given",
                "confidence",
                "human_review_required",
            ],
        }

    def get_scripted_intake_definition(self) -> ScriptedIntakeDefinition:
        return ScriptedIntakeDefinition(
            intro_text=BIRCHWOOD_COLLISION_INTRO,
            stages=[
                _stage("DRIVABILITY_CHECK", "is_drivable", "text"),
                _stage("DAMAGE_TYPE", "damage_type", "free_text"),
                _stage("VEHICLE_YEAR", "vehicle_year", "integer"),
                _stage("REBUILT_SALVAGE_STATUS", "rebuilt_salvage_status", "text"),
                _stage("CALLER_NAME", "caller_name", "text", sensitivity="pii"),
                _stage("PHONE", "phone", "phone", sensitivity="pii"),
                _stage("EMAIL", "email", "text", sensitivity="pii"),
                _stage("ADDRESS", "address", "text", sensitivity="pii"),
                _stage("VEHICLE_MAKE", "vehicle_make", "text"),
                _stage("VEHICLE_MODEL", "vehicle_model", "text"),
                _stage("LICENSE_PLATE", "license_plate", "text", required=False),
                _stage("INCIDENT_DESCRIPTION", "incident_description", "free_text"),
                _stage(
                    "INCIDENT_DATETIME", "incident_datetime", "text", required=False
                ),
                _stage("FILING_INSURANCE_CLAIM", "filing_insurance_claim", "text"),
                _stage("CLAIM_NUMBER", "claim_number", "text", required=False),
                _stage(
                    "PREFERRED_COLLISION_CENTER",
                    "preferred_collision_center",
                    "text",
                    required=False,
                    hints=(
                        "BIRCHWOOD_COLLISION_LOCATION_1,"
                        "BIRCHWOOD_COLLISION_LOCATION_2,"
                        "BIRCHWOOD_COLLISION_LOCATION_3"
                    ),
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
        session.channel_metadata.setdefault("automotive_collision", {})
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
        session.channel_metadata.setdefault(
            "route_metadata",
            {
                "display_name": BIRCHWOOD_COLLISION_DISPLAY_NAME,
                "powered_by": BIRCHWOOD_COLLISION_POWERED_BY,
                "client_target": BIRCHWOOD_COLLISION_CLIENT_TARGET,
                "status": BIRCHWOOD_COLLISION_STATUS,
            },
        )


def _stage(
    stage_id: str,
    field_name: str,
    field_type: str,
    *,
    required: bool = True,
    sensitivity: str | None = None,
    hints: str | None = None,
) -> ScriptedStageDefinition:
    speech_profile, timeout_seconds, speech_timeout = _speech_settings_for_stage(
        field_name=field_name,
    )
    return ScriptedStageDefinition(
        stage_id=stage_id,
        field_name=field_name,
        prompt=BIRCHWOOD_COLLISION_PROMPTS[field_name],
        field_type=field_type,
        expected_answer_type=field_type,
        required=required,
        sensitivity=sensitivity,
        hints=hints,
        speech_profile=speech_profile,
        timeout_seconds=timeout_seconds,
        speech_timeout=speech_timeout,
    )


def _speech_settings_for_stage(field_name: str) -> tuple[str, int, str]:
    if (
        field_name == "incident_description"
        and config.BIRCHWOOD_ALLOW_LONG_INCIDENT_DESCRIPTION
    ):
        return (
            "narrative",
            config.BIRCHWOOD_NARRATIVE_TIMEOUT_SECONDS,
            config.BIRCHWOOD_NARRATIVE_SPEECH_TIMEOUT_SECONDS,
        )

    return (
        "short_field",
        config.BIRCHWOOD_SHORT_FIELD_TIMEOUT_SECONDS,
        "3",
    )


def _intake_from_session(session: OrchestratorSession) -> AutomotiveCollisionIntake:
    scripted = session.channel_metadata.get("scripted_intake") or {}
    fields = scripted.get("fields") or {}
    vehicle_year_raw = _clean(
        fields.get("vehicle_year_raw") or fields.get("vehicle_year")
    )
    drivable_raw = _clean(fields.get("drivable_raw") or fields.get("is_drivable"))
    rebuilt_raw = _clean(
        fields.get("rebuilt_salvage_raw") or fields.get("rebuilt_salvage_status")
    )
    insurance_raw = _clean(
        fields.get("insurance_claim_raw") or fields.get("filing_insurance_claim")
    )
    damage_type = _clean(fields.get("damage_type"))
    damage_profile = _damage_booleans(damage_type)
    return AutomotiveCollisionIntake(
        caller_name=_clean(fields.get("caller_name")),
        phone=_clean(fields.get("phone") or fields.get("callback_phone")),
        email=_clean(fields.get("email")),
        address=_clean(fields.get("address")),
        vehicle_year=parse_vehicle_year(fields.get("vehicle_year")),
        vehicle_year_raw=vehicle_year_raw,
        vehicle_make=_clean(fields.get("vehicle_make")),
        vehicle_model=_clean(fields.get("vehicle_model")),
        license_plate=_clean(fields.get("license_plate")),
        is_drivable=_coalesce_bool(
            fields.get("is_drivable"),
            parse_intake_bool(drivable_raw, kind="drivable"),
        ),
        drivable_raw=drivable_raw,
        damage_type=damage_type,
        glass_only=_coalesce_bool(fields.get("glass_only"), damage_profile["glass"]),
        body_damage=_coalesce_bool(fields.get("body_damage"), damage_profile["body"]),
        incident_description=_clean(fields.get("incident_description")),
        incident_datetime=_clean(fields.get("incident_datetime")),
        filing_insurance_claim=_coalesce_bool(
            fields.get("filing_insurance_claim"),
            parse_intake_bool(insurance_raw, kind="insurance"),
        ),
        insurance_claim_raw=insurance_raw,
        claim_number=_claim_number(fields.get("claim_number")),
        preferred_collision_center=_clean(fields.get("preferred_collision_center")),
        is_rebuilt_or_salvage=_coalesce_bool(
            fields.get("is_rebuilt_or_salvage"),
            parse_intake_bool(rebuilt_raw, kind="rebuilt"),
        ),
        rebuilt_salvage_raw=rebuilt_raw,
        already_spoke_to_someone=_truthy(fields.get("already_spoke_to_someone")),
        multiple_vehicles=_truthy(fields.get("multiple_vehicles")),
        caller_requested_transfer=_truthy(fields.get("caller_requested_transfer")),
    )


def _intake_record(
    context: WorkflowContext,
    session: OrchestratorSession,
    intake: AutomotiveCollisionIntake,
    assessment: AutomotiveCollisionAssessment,
) -> AutomotiveCollisionRecord:
    transcript = _conversation_transcript(session)
    return AutomotiveCollisionRecord(
        intake_id=str(uuid.uuid4()),
        timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        workflow_id=context.workflow_id,
        vertical=context.vertical,
        powered_by=BIRCHWOOD_COLLISION_POWERED_BY,
        client_target=BIRCHWOOD_COLLISION_CLIENT_TARGET,
        workflow_status=BIRCHWOOD_COLLISION_STATUS,
        status=assessment.status,
        customer={
            "name": intake.caller_name,
            "phone": intake.phone,
            "email": intake.email,
            "address": intake.address,
        },
        vehicle={
            "year": intake.vehicle_year,
            "make": intake.vehicle_make,
            "model": intake.vehicle_model,
            "license_plate": intake.license_plate,
            "is_luxury": assessment.is_luxury,
            "is_rebuilt": intake.is_rebuilt_or_salvage,
            "is_rebuilt_or_salvage": intake.is_rebuilt_or_salvage,
            "is_drivable": intake.is_drivable,
        },
        incident={
            "damage_type": intake.damage_type,
            "is_drivable": intake.is_drivable,
            "description": intake.incident_description,
            "incident_datetime": intake.incident_datetime,
            "glass_only": bool(intake.glass_only),
            "body_damage": bool(intake.body_damage),
        },
        insurance={
            "filing_claim": intake.filing_insurance_claim,
            "filing_insurance_claim": intake.filing_insurance_claim,
            "claim_number": intake.claim_number,
            "private_pay": assessment.private_pay,
        },
        location={
            "preferred_center": assessment.preferred_collision_center,
            "available_centers": assessment.available_collision_centers,
        },
        flags=list(assessment.flags),
        missing_information=list(assessment.missing_information),
        conversation_transcript=transcript,
        outcome={
            "result": assessment.result,
            "reason": assessment.reason,
            "outcome": assessment.outcome,
        },
        caller_name=intake.caller_name,
        phone=intake.phone,
        email=intake.email,
        address=intake.address,
        vehicle_year=intake.vehicle_year,
        vehicle_make=intake.vehicle_make,
        vehicle_model=intake.vehicle_model,
        license_plate=intake.license_plate,
        is_drivable=intake.is_drivable,
        damage_type=intake.damage_type,
        glass_only=bool(intake.glass_only),
        body_damage=bool(intake.body_damage),
        incident_description=intake.incident_description,
        filing_insurance_claim=intake.filing_insurance_claim,
        claim_number=intake.claim_number,
        private_pay=assessment.private_pay,
        preferred_collision_center=assessment.preferred_collision_center,
        available_collision_centers=assessment.available_collision_centers,
        is_luxury=assessment.is_luxury,
        is_vw=assessment.is_vw,
        is_rebuilt_or_salvage=intake.is_rebuilt_or_salvage,
        recommended_routing=assessment.recommended_routing,
        transfer_department=assessment.transfer_department,
        decline_reason=assessment.decline_reason,
        callback_needed=assessment.callback_needed,
        transcript_summary=_transcript_summary(intake, assessment),
        disclaimers_given=assessment.disclaimers_given,
        confidence=assessment.confidence,
        human_review_required=assessment.human_review_required,
    )


def _summary(record: AutomotiveCollisionRecord) -> str:
    vehicle = " ".join(
        str(part)
        for part in [
            record.vehicle_year,
            record.vehicle_make,
            record.vehicle_model,
        ]
        if part
    )
    return (
        f"{record.recommended_routing} Birchwood collision intake for "
        f"{vehicle or 'unknown vehicle'} with "
        f"{record.damage_type or 'unknown damage'}."
    )


def _spoken_final_message(
    disposition: str,
    final_result: WorkflowFinalResult,
) -> str:
    record = final_result.structured_output.get("intake_record", {})
    flags = set(record.get("flags") or [])
    if disposition == "TRANSFER_COLLISION_CENTER":
        return (
            "Because the vehicle may not be safe to drive or you asked for help, "
            "I will route this to the collision team now."
        )
    if disposition == "TRANSFER_GLASS_DEPARTMENT":
        return (
            "It sounds like glass-only damage, so I will route this to the "
            "glass department."
        )
    if disposition == "DECLINED_VEHICLE_YEAR":
        return (
            "I appreciate you calling. Unfortunately, our collision centers handle "
            "vehicles 2012 and newer. Thanks for thinking of Birchwood."
        )
    if disposition == "DECLINED_REBUILT_SALVAGE":
        return (
            "Thanks for letting me know. Our collision centers aren't able to "
            "service rebuilt or salvage title vehicles. I appreciate you calling."
        )
    if "private_pay" in flags:
        return (
            "No problem, I'll mark this as private pay. I have collected the "
            "intake details for staff follow-up."
        )
    if "missing_claim_number" in flags:
        return (
            "That's okay. I'll note that the claim number is still needed so "
            "the team can follow up."
        )
    if disposition == "INCOMPLETE_CALLBACK_NEEDED":
        return (
            "Thanks. I have recorded the details I have and marked this for a "
            "callback to confirm the missing information."
        )
    if disposition == "HUMAN_REVIEW":
        return (
            "Thanks. I have marked this for staff review because a few details "
            "need confirmation."
        )
    return (
        "Thanks, I have the main details noted. The Birchwood Collision team "
        "will be able to review this intake and follow up with you. Just a "
        "reminder, this doesn't confirm coverage, pricing, or an appointment "
        "yet - the team will confirm the next steps."
    )


def _conversation_transcript(session: OrchestratorSession) -> str:
    return "\n".join(
        f"{turn.role}: {turn.text}" for turn in session.conversation if turn.text
    )


def _transcript_summary(
    intake: AutomotiveCollisionIntake,
    assessment: AutomotiveCollisionAssessment,
) -> str:
    return (
        f"{assessment.outcome} for {intake.vehicle_year or 'unknown year'} "
        f"{intake.vehicle_make or 'unknown make'} "
        f"{intake.vehicle_model or 'unknown model'}."
    )


def _damage_booleans(damage_type: str | None) -> dict[str, bool]:
    text = (damage_type or "").lower()
    glass = any(word in text for word in ["glass", "windshield", "window"])
    body = any(
        word in text
        for word in [
            "body",
            "bumper",
            "fender",
            "door",
            "hood",
            "trunk",
            "panel",
            "dent",
            "scratch",
            "front",
            "rear",
            "side",
        ]
    )
    return {"glass": glass, "body": body}


def _claim_number(value: Any) -> str | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    if cleaned.lower() in {"no", "none", "not yet", "no claim number"}:
        return None
    return cleaned


def _coalesce_bool(raw: Any, parsed: bool | None) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if parsed is not None:
        return parsed
    return _truthy(raw) if raw in (0, 1) else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"true", "1", "yes", "y", "yeah", "yep"}


def _first_flag_for_rule(flags: list[str]) -> str | None:
    return flags[0] if flags else None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
