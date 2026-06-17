"""Minimal Birchwood collision-intake workflow: pure intake -> specialist handoff.

Deterministic (no LLM). Collects only what a collision specialist needs and hands
off to a human. It does NOT estimate, decide, advise, triage, or adjudicate. The
generic reactive emergency reflex (caller spontaneously signals being hurt) is the
SHARED platform baseline applied by the WorkflowEngine overlay - this workflow
never asks about injuries and adds no clinical logic of its own.
"""

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
from src.verticals.collision_intake_min.constants import (
    COLLISION_MIN_CLIENT_TARGET,
    COLLISION_MIN_DEFLECTION_REPLY,
    COLLISION_MIN_DISPLAY_NAME,
    COLLISION_MIN_DISPOSITIONS,
    COLLISION_MIN_OUTPUT_TYPE,
    COLLISION_MIN_POWERED_BY,
    COLLISION_MIN_REQUIRED_FIELDS,
    COLLISION_MIN_STATUS,
    COLLISION_MIN_VERTICAL,
    COLLISION_MIN_WORKFLOW_ID,
    COLLISION_MIN_WORKFLOW_VERSION,
)
from src.verticals.collision_intake_min.prompts import (
    COLLISION_MIN_CALLBACK_CLOSE,
    COLLISION_MIN_HANDOFF_CLOSE,
    COLLISION_MIN_INTRO,
    COLLISION_MIN_PROMPTS,
)
from src.verticals.collision_intake_min.rules import (
    classify_collision_min_intake,
    clean_claim_number,
    detect_estimate_request,
    parse_claim_opened,
    parse_drivable_status,
    parse_vehicle_year,
)
from src.verticals.collision_intake_min.schemas import (
    CollisionMinAssessment,
    CollisionMinIntake,
    CollisionMinRecord,
)


class BirchwoodCollisionMinIntakeWorkflow(BaseWorkflow):
    """Deterministic minimal collision intake -> specialist handoff."""

    def get_definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            workflow_id=COLLISION_MIN_WORKFLOW_ID,
            vertical=COLLISION_MIN_VERTICAL,
            version=COLLISION_MIN_WORKFLOW_VERSION,
            display_name=COLLISION_MIN_DISPLAY_NAME,
            required_fields=list(COLLISION_MIN_REQUIRED_FIELDS),
            supported_output_types=[COLLISION_MIN_OUTPUT_TYPE],
            default_output_type=COLLISION_MIN_OUTPUT_TYPE,
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
        session.channel_metadata.setdefault("collision_intake_min", {})
        return session.model_dump(mode="json")

    async def handle_turn(
        self,
        context: WorkflowContext,
        input: WorkflowInput,
    ) -> WorkflowTurnResult:
        session = self._load_session(context, input.session_state)
        intake = _intake_from_session(session)
        assessment = classify_collision_min_intake(intake, dynamic_text=input.user_text)
        final_result = self.build_final_result_from_session(
            context, session, assessment=assessment
        )
        session.channel_metadata["workflow_final_result"] = final_result.model_dump(
            mode="json"
        )
        session.channel_metadata["stage"] = "FINAL"
        session.is_finalized = True
        session.finalization_reason = "collision_intake_min_complete"

        return WorkflowTurnResult(
            assistant_text=_spoken_final_message(assessment, input.user_text),
            stage="FINAL",
            should_continue=False,
            should_finalize=True,
            # Pure intake never auto-escalates; the shared platform injury overlay
            # handles the emergency reflex on top of this result.
            escalation_required=False,
            recommended_disposition=assessment.disposition,
            confidence_score=assessment.confidence,
            rules_triggered=list(assessment.rules_triggered),
            safety_events=[],
            updated_state=session.model_dump(mode="json"),
            audit_metadata={
                "workflow_id": context.workflow_id,
                "deterministic": True,
                "output_type": COLLISION_MIN_OUTPUT_TYPE,
                "finalization_reason": "collision_intake_min_complete",
                "collision_min_missing_information": assessment.missing_information,
                "collision_min_handoff_mode": assessment.handoff_mode,
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
        assessment: CollisionMinAssessment | None = None,
    ) -> WorkflowFinalResult:
        self._apply_context(session, context)
        intake = _intake_from_session(session)
        if assessment is None:
            assessment = classify_collision_min_intake(intake)
        record = _intake_record(context, intake, assessment)
        session.channel_metadata["collision_intake_min"] = record.model_dump(
            mode="json"
        )

        return WorkflowFinalResult(
            final_disposition=assessment.disposition,
            confidence_score=assessment.confidence,
            summary=record.handoff_summary,
            structured_output={
                "output_type": COLLISION_MIN_OUTPUT_TYPE,
                "intake_record": record.model_dump(mode="json"),
                "intake": intake.model_dump(mode="json"),
                "disposition_taxonomy": COLLISION_MIN_DISPOSITIONS,
            },
            safety_events=[],
            rules_triggered=list(assessment.rules_triggered),
            audit_metadata={
                "workflow_id": context.workflow_id,
                "deterministic": True,
                "rules_engine": "collision_intake_min_rules_v1",
                "finalization_reason": "collision_intake_min_complete",
                "collision_min_missing_information": assessment.missing_information,
                "collision_min_handoff_mode": assessment.handoff_mode,
                "disclaimers_given": assessment.disclaimers_given,
            },
        )

    def get_extraction_schema(self) -> dict[str, Any] | None:
        return {
            "schema_version": "collision_intake_min_extraction_v1",
            "entities": [
                "workflow_id",
                "vertical",
                "caller_name",
                "callback_number",
                "vehicle_year",
                "vehicle_make",
                "vehicle_model",
                "vehicle_color",
                "license_plate",
                "vin",
                "damage_description",
                "drivable_status",
                "vehicle_location",
                "mpi_claim_opened",
                "mpi_claim_number",
                "flags",
                "missing_information",
                "recommended_routing",
                "handoff_mode",
                "confidence",
                "human_review_required",
                "disclaimers_given",
            ],
        }

    def get_scripted_intake_definition(self) -> ScriptedIntakeDefinition:
        return ScriptedIntakeDefinition(
            intro_text=COLLISION_MIN_INTRO,
            stages=[
                ScriptedStageDefinition(
                    stage_id="CALLER_NAME",
                    field_name="caller_name",
                    prompt=COLLISION_MIN_PROMPTS["caller_name"],
                    field_type="text",
                    expected_answer_type="text",
                    sensitivity="pii",
                ),
                ScriptedStageDefinition(
                    stage_id="CALLBACK_NUMBER",
                    field_name="callback_number",
                    prompt=COLLISION_MIN_PROMPTS["callback_number"],
                    field_type="phone",
                    expected_answer_type="phone",
                    sensitivity="pii",
                ),
                ScriptedStageDefinition(
                    stage_id="VEHICLE_YEAR",
                    field_name="vehicle_year",
                    prompt=COLLISION_MIN_PROMPTS["vehicle_year"],
                    field_type="integer",
                    expected_answer_type="integer",
                ),
                ScriptedStageDefinition(
                    stage_id="VEHICLE_MAKE",
                    field_name="vehicle_make",
                    prompt=COLLISION_MIN_PROMPTS["vehicle_make"],
                    field_type="text",
                    expected_answer_type="text",
                ),
                ScriptedStageDefinition(
                    stage_id="VEHICLE_MODEL",
                    field_name="vehicle_model",
                    prompt=COLLISION_MIN_PROMPTS["vehicle_model"],
                    field_type="text",
                    expected_answer_type="text",
                ),
                ScriptedStageDefinition(
                    stage_id="DAMAGE_DESCRIPTION",
                    field_name="damage_description",
                    prompt=COLLISION_MIN_PROMPTS["damage_description"],
                    field_type="free_text",
                    expected_answer_type="free_text",
                    timeout_seconds=10,
                    speech_timeout="auto",
                ),
                ScriptedStageDefinition(
                    stage_id="DRIVABLE_STATUS",
                    field_name="drivable_status",
                    prompt=COLLISION_MIN_PROMPTS["drivable_status"],
                    field_type="text",
                    expected_answer_type="text",
                ),
                ScriptedStageDefinition(
                    stage_id="MPI_CLAIM",
                    field_name="mpi_claim",
                    prompt=COLLISION_MIN_PROMPTS["mpi_claim"],
                    field_type="text",
                    expected_answer_type="text",
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
        session.channel_metadata.setdefault("collision_intake_min", {})
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


def _intake_from_session(session: OrchestratorSession) -> CollisionMinIntake:
    scripted = session.channel_metadata.get("scripted_intake") or {}
    fields = scripted.get("fields") or {}
    # The scripted "mpi_claim" stage captures a single utterance; parse the
    # opened/number out of it deterministically.
    mpi_raw = _clean(fields.get("mpi_claim")) or _clean(fields.get("mpi_claim_raw"))
    mpi_opened = fields.get("mpi_claim_opened")
    if mpi_opened is None and mpi_raw is not None:
        mpi_opened = parse_claim_opened(mpi_raw)
    mpi_number = _clean(fields.get("mpi_claim_number")) or clean_claim_number(mpi_raw)

    drivable = _clean(fields.get("drivable_status"))
    drivable_status = (
        drivable
        if drivable in {"drivable", "not_drivable", "unknown"}
        else parse_drivable_status(drivable)
    )

    return CollisionMinIntake(
        caller_name=_clean(fields.get("caller_name")),
        callback_number=_clean(fields.get("callback_number")),
        vehicle_year=parse_vehicle_year(fields.get("vehicle_year")),
        vehicle_year_raw=_clean(fields.get("vehicle_year")),
        vehicle_make=_clean(fields.get("vehicle_make")),
        vehicle_model=_clean(fields.get("vehicle_model")),
        vehicle_color=_clean(fields.get("vehicle_color")),
        license_plate=_clean(fields.get("license_plate")),
        vin=_clean(fields.get("vin")),
        damage_description=_clean(fields.get("damage_description")),
        drivable_status=drivable_status,
        drivable_raw=drivable,
        vehicle_location=_clean(fields.get("vehicle_location")),
        mpi_claim_opened=mpi_opened,
        mpi_claim_raw=mpi_raw,
        mpi_claim_number=mpi_number,
    )


def _intake_record(
    context: WorkflowContext,
    intake: CollisionMinIntake,
    assessment: CollisionMinAssessment,
) -> CollisionMinRecord:
    return CollisionMinRecord(
        workflow_id=context.workflow_id,
        vertical=context.vertical,
        powered_by=COLLISION_MIN_POWERED_BY,
        client_target=COLLISION_MIN_CLIENT_TARGET,
        status=COLLISION_MIN_STATUS,
        caller_name=intake.caller_name,
        callback_number=intake.callback_number,
        vehicle_year=intake.vehicle_year,
        vehicle_make=intake.vehicle_make,
        vehicle_model=intake.vehicle_model,
        vehicle_color=intake.vehicle_color,
        license_plate=intake.license_plate,
        vin=intake.vin,
        damage_description=intake.damage_description,
        drivable_status=intake.drivable_status,
        vehicle_location=intake.vehicle_location,
        mpi_claim_opened=intake.mpi_claim_opened,
        mpi_claim_number=intake.mpi_claim_number,
        flags=list(assessment.flags),
        missing_information=list(assessment.missing_information),
        recommended_routing=assessment.disposition,
        handoff_mode=assessment.handoff_mode,
        handoff_summary=_handoff_summary(intake, assessment),
        disclaimers_given=list(assessment.disclaimers_given),
        confidence=assessment.confidence,
        human_review_required=assessment.human_review_required,
    )


def _handoff_summary(
    intake: CollisionMinIntake,
    assessment: CollisionMinAssessment,
) -> str:
    """Plain structured handoff for the collision specialist (not an SBAR)."""
    vehicle = " ".join(
        str(p)
        for p in [intake.vehicle_year, intake.vehicle_make, intake.vehicle_model]
        if p
    )
    drivable = {
        "drivable": "drivable",
        "not_drivable": "NOT drivable (needs tow)",
        "unknown": "drivability unknown",
    }.get(intake.drivable_status or "", "drivability unknown")
    if intake.mpi_claim_opened is True:
        claim = f"MPI claim {intake.mpi_claim_number or '(number to follow)'}"
    elif intake.mpi_claim_opened is False:
        claim = "no MPI claim opened yet"
    else:
        claim = "MPI claim status unknown"
    location = (
        f" Location: {intake.vehicle_location}." if intake.vehicle_location else ""
    )
    return (
        f"CALLER: {intake.caller_name or 'unknown'}, callback "
        f"{intake.callback_number or 'unknown'}. "
        f"VEHICLE: {vehicle or 'unknown'} ({drivable}).{location} "
        f"DAMAGE: {intake.damage_description or 'unknown'}. "
        f"INSURANCE: {claim}. "
        f"HANDOFF: {assessment.disposition} "
        f"(missing: {', '.join(assessment.missing_information) or 'none'})."
    )


def _spoken_final_message(
    assessment: CollisionMinAssessment,
    user_text: str,
) -> str:
    """Closing line. Deflects estimate/coverage/cost questions to the specialist;
    never estimates, decides, or promises anything."""
    prefix = ""
    if detect_estimate_request(user_text):
        prefix = COLLISION_MIN_DEFLECTION_REPLY + " "
    if assessment.disposition == "CALLBACK_NEEDED":
        return prefix + COLLISION_MIN_CALLBACK_CLOSE
    return prefix + COLLISION_MIN_HANDOFF_CLOSE


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
