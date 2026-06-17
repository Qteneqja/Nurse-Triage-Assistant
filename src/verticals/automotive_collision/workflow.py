"""ORCA automotive collision intake workflow customized for Birchwood demo."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import src.config as config
from src.orchestrator.schemas import OrchestratorSession
from src.platform.workflows.schemas import (
    ScriptedIntakeDefinition,
    ScriptedStageDefinition,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowFinalResult,
    WorkflowInput,
    WorkflowTurnResult,
)
from src.platform.workflows.spec_workflow import SpecDrivenWorkflow
from src.safety.injury_detection import scan_for_injuries
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
from src.verticals.automotive_collision.narrative_extraction import (
    extract_from_narrative,
)
from src.verticals.automotive_collision.prompts import (
    BIRCHWOOD_COLLISION_INTRO,
    BIRCHWOOD_COLLISION_NEXT_STEPS_CLOSE,
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


class BirchwoodCollisionIntakeWorkflow(SpecDrivenWorkflow):
    """Deterministic Birchwood-targeted collision intake workflow powered by ORCA.

    Runs on the spec-driven engine (PR 3): the complete declarative
    definition lives in ``build_birchwood_spec()`` and the vertical's rich
    behaviors are registered as named hooks. The overrides below keep the
    Birchwood record/message shapes byte-identical to the pre-engine
    implementation; the platform safety overlay applies above this class
    either way.
    """

    def __init__(self) -> None:
        from src.verticals.automotive_collision.spec import build_birchwood_spec

        super().__init__(build_birchwood_spec())

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
            "entities": list(BIRCHWOOD_EXTRACTION_ENTITIES),
        }

    def get_scripted_intake_definition(self) -> ScriptedIntakeDefinition:
        """Narrative-first conversation (PR 2).

        The caller tells the whole story first; deterministic extraction
        prefills every field the story answered, and the remaining stages
        are skipped automatically when already filled — so the follow-ups
        are targeted gap-fill of REQUIRED fields only. Optional details
        (email, plate, photos, police report, other parties, preferred
        location/timing) are captured opportunistically from the story and
        never interrogated one-by-one.
        """
        return ScriptedIntakeDefinition(
            intro_text=BIRCHWOOD_COLLISION_INTRO,
            stages=_scripted_stage_definitions(),
        )

    # ------------------------------------------------------------------
    # PR 2 conversation hooks (deterministic — no LLM anywhere)
    # ------------------------------------------------------------------

    def prefill_from_narrative(
        self,
        session: OrchestratorSession,
        stage: ScriptedStageDefinition,
        narrative: str,
    ) -> dict[str, Any]:
        if stage.field_name != "incident_description":
            return {}
        prefill = extract_from_narrative(narrative)
        if prefill.audit:
            session.channel_metadata["narrative_extraction"] = {
                "schema": "birchwood_narrative_extraction_v1",
                "prefilled": prefill.audit,
            }
        return prefill.fields

    def on_field_recorded(
        self,
        session: OrchestratorSession,
        stage: ScriptedStageDefinition,
        value: Any,
    ) -> dict[str, Any]:
        field = stage.field_name
        if field == "injuries_state":
            return {"injuries_state": _normalize_injuries_answer(value)}
        if field == "filing_insurance_claim":
            parsed = (
                value
                if isinstance(value, bool)
                else parse_intake_bool(str(value or ""), kind="insurance")
            )
            if parsed is False:
                # Private pay deterministically answers the claim questions.
                return {
                    "insurance_provider": "private pay",
                    "claim_number": "none",
                }
        if field == "confirmation_ack":
            normalized = _normalize_yes_no_answer(value)
            extra: dict[str, Any] = {"confirmation_ack": normalized}
            if normalized == "yes":
                # Confirmed readback — skip the correction stage.
                extra["correction_note"] = "none"
            return extra
        return {}

    def build_dynamic_prompt(
        self,
        session: OrchestratorSession,
        stage: ScriptedStageDefinition,
    ) -> str | None:
        if stage.field_name != "confirmation_ack":
            return None
        intake = _intake_from_session(session)
        parts: list[str] = []
        vehicle = " ".join(
            str(p)
            for p in [intake.vehicle_year, intake.vehicle_make, intake.vehicle_model]
            if p
        )
        if vehicle:
            parts.append(f"a {vehicle}")
        if intake.is_drivable is True:
            parts.append("still safe to drive")
        elif intake.is_drivable is False:
            parts.append("not safe to drive")
        if intake.damage_type:
            parts.append(f"damage to the {intake.damage_type}")
        if intake.incident_datetime:
            parts.append(f"it happened {intake.incident_datetime}")
        if intake.incident_location:
            parts.append(f"around {intake.incident_location}")
        if intake.filing_insurance_claim is True:
            claim = (
                f" with claim number {intake.claim_number}"
                if intake.claim_number
                else ", claim number to follow"
            )
            provider = intake.insurance_provider or "insurance"
            parts.append(f"going through {provider}{claim}")
        elif intake.filing_insurance_claim is False:
            parts.append("paying privately")
        contact = " ".join(
            p for p in [intake.caller_name or "", intake.phone or ""] if p
        ).strip()
        if contact:
            parts.append(f"and your advisor should call {contact}")
        summary = "; ".join(parts) if parts else "the details you gave me"
        return (
            "Wonderful - let me just make sure I've got everything right: "
            f"{summary}. Did I get all of that right?"
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


# Shared with the WorkflowSpec (src/verticals/automotive_collision/spec.py).
BIRCHWOOD_EXTRACTION_ENTITIES: list[str] = [
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
    "incident_datetime",
    "incident_location",
    "injuries_state",
    "other_parties",
    "police_report_filed",
    "photos_available",
    "filing_insurance_claim",
    "insurance_provider",
    "claim_number",
    "private_pay",
    "preferred_collision_center",
    "preferred_timing",
    "urgency",
    "confirmation_ack",
    "correction_note",
    "plain_summary",
    "shop_summary",
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
]


def _scripted_stage_definitions() -> list[ScriptedStageDefinition]:
    """Birchwood stage list — shared by the workflow and its WorkflowSpec.

    Built at call time because voice timeouts/profiles read live config.
    """
    # Minimal pure-intake: only what a collision specialist needs, then hand
    # off. NO injury question (the reactive emergency reflex is the shared
    # platform overlay), no rebuilt/salvage, no narrative, no datetime/location
    # gap-fill, no readback — those were the richer "old script" and are gone.
    return [
        _stage("CALLER_NAME", "caller_name", "text", sensitivity="pii"),
        _stage("PHONE", "phone", "phone", sensitivity="pii"),
        _stage("VEHICLE_YEAR", "vehicle_year", "integer"),
        _stage("VEHICLE_MAKE", "vehicle_make", "text"),
        _stage("VEHICLE_MODEL", "vehicle_model", "text"),
        _stage("DAMAGE_TYPE", "damage_type", "free_text"),
        _stage("DRIVABILITY_CHECK", "is_drivable", "text"),
        _stage(
            "FILING_INSURANCE_CLAIM", "filing_insurance_claim", "text", required=False
        ),
        _stage("CLAIM_NUMBER", "claim_number", "text", required=False),
    ]


def _stage(
    stage_id: str,
    field_name: str,
    field_type: str,
    *,
    required: bool = True,
    sensitivity: str | None = None,
    hints: str | None = None,
    dynamic_prompt: bool = False,
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
        # Narrative stages keep listening across utterances so a caller
        # telling a long collision story is never cut off mid-thought.
        multi_segment=(speech_profile == "narrative"),
        dynamic_prompt=dynamic_prompt,
    )


def _normalize_injuries_answer(value: Any) -> str:
    """Map a spoken injury answer to 'reported' | 'denied' (fail closed)."""
    if isinstance(value, str) and value in ("reported", "denied"):
        return value
    text = str(value or "").strip().lower()
    scan = scan_for_injuries(text)
    if scan.mentioned:
        return "reported"
    if scan.denied:
        return "denied"
    if re.search(r"\b(no|nope|nobody|no one|none|negative|we're good)\b", text):
        return "denied"
    if re.search(r"\b(yes|yeah|yep|a (?:little|bit)|kind of|sort of)\b", text):
        return "reported"
    # Unclear answer to a direct injury question — fail closed.
    return "reported"


def _normalize_yes_no_answer(value: Any) -> str:
    text = str(value or "").strip().lower()
    # Negations first — "no, that's not right" must never read as "yes".
    if re.search(
        r"\b(no|nope|nah|not (?:quite|right|correct)|wrong|incorrect)\b", text
    ):
        return "no"
    if re.search(
        r"\b(yes|yeah|yep|correct|exactly|perfect"
        r"|that'?s right|sounds right|you got it|all (?:right|good))\b",
        text,
    ):
        return "yes"
    return text or "unclear"


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
    return _intake_from_fields(scripted.get("fields") or {})


def _intake_from_fields(fields: dict) -> AutomotiveCollisionIntake:
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
        incident_location=_clean(fields.get("incident_location")),
        injuries_state=_clean(fields.get("injuries_state")),
        other_parties=_clean(fields.get("other_parties")),
        police_report_filed=_clean(fields.get("police_report_filed")),
        photos_available=_clean(fields.get("photos_available")),
        filing_insurance_claim=_coalesce_bool(
            fields.get("filing_insurance_claim"),
            parse_intake_bool(insurance_raw, kind="insurance"),
        ),
        insurance_claim_raw=insurance_raw,
        insurance_provider=_clean(fields.get("insurance_provider")),
        claim_number=_claim_number(fields.get("claim_number")),
        preferred_collision_center=_clean(fields.get("preferred_collision_center")),
        preferred_timing=_clean(fields.get("preferred_timing")),
        urgency=_clean(fields.get("urgency")),
        confirmation_ack=_clean(fields.get("confirmation_ack")),
        correction_note=_clean(fields.get("correction_note")),
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
            "incident_location": intake.incident_location,
            "injuries_state": intake.injuries_state,
            "other_parties": intake.other_parties,
            "police_report_filed": intake.police_report_filed,
            "photos_available": intake.photos_available,
            "glass_only": bool(intake.glass_only),
            "body_damage": bool(intake.body_damage),
        },
        insurance={
            "filing_claim": intake.filing_insurance_claim,
            "filing_insurance_claim": intake.filing_insurance_claim,
            "insurance_provider": intake.insurance_provider,
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
        incident_datetime=intake.incident_datetime,
        incident_location=intake.incident_location,
        injuries_state=intake.injuries_state,
        other_parties=intake.other_parties,
        police_report_filed=intake.police_report_filed,
        photos_available=intake.photos_available,
        filing_insurance_claim=intake.filing_insurance_claim,
        insurance_provider=intake.insurance_provider,
        claim_number=intake.claim_number,
        private_pay=assessment.private_pay,
        preferred_collision_center=assessment.preferred_collision_center,
        preferred_timing=intake.preferred_timing,
        urgency=intake.urgency,
        confirmation_ack=intake.confirmation_ack,
        correction_note=_clean_correction(intake.correction_note),
        available_collision_centers=assessment.available_collision_centers,
        is_luxury=assessment.is_luxury,
        is_vw=assessment.is_vw,
        is_rebuilt_or_salvage=intake.is_rebuilt_or_salvage,
        recommended_routing=assessment.recommended_routing,
        transfer_department=assessment.transfer_department,
        decline_reason=assessment.decline_reason,
        callback_needed=assessment.callback_needed,
        transcript_summary=_transcript_summary(intake, assessment),
        plain_summary=_plain_summary(intake, assessment),
        shop_summary=_shop_summary(intake, assessment),
        disclaimers_given=assessment.disclaimers_given,
        confidence=assessment.confidence,
        human_review_required=assessment.human_review_required,
    )


def _clean_correction(value: str | None) -> str | None:
    if not value or value.strip().lower() in {"none", "n/a", "no"}:
        return None
    return value.strip()


def _plain_summary(
    intake: AutomotiveCollisionIntake,
    assessment: AutomotiveCollisionAssessment,
) -> str:
    """Plain-language recap suitable for reading to the caller."""
    vehicle = " ".join(
        str(p)
        for p in [intake.vehicle_year, intake.vehicle_make, intake.vehicle_model]
        if p
    )
    bits = [f"We've taken down your collision details for the {vehicle or 'vehicle'}."]
    if intake.injuries_state == "reported":
        bits.append(
            "You mentioned someone may be hurt - please get medical attention "
            "or call 9 1 1 if needed; our team has been alerted."
        )
    if intake.is_drivable is False:
        bits.append("The vehicle isn't safe to drive.")
    if intake.filing_insurance_claim is True:
        provider = intake.insurance_provider or "your insurer"
        bits.append(f"The repair will be coordinated with {provider}.")
    elif intake.filing_insurance_claim is False:
        bits.append("The repair will be out of pocket.")
    if intake.phone:
        bits.append(
            f"A Birchwood service advisor will call you back at {intake.phone}."
        )
    else:
        bits.append("A Birchwood service advisor will follow up with you.")
    bits.append(
        "This doesn't confirm coverage, pricing, or an appointment yet - "
        "your advisor will confirm next steps."
    )
    return " ".join(bits)


def _shop_summary(
    intake: AutomotiveCollisionIntake,
    assessment: AutomotiveCollisionAssessment,
) -> str:
    """Shop-facing handoff — the Birchwood analogue of SBAR."""
    vehicle = " ".join(
        str(p)
        for p in [intake.vehicle_year, intake.vehicle_make, intake.vehicle_model]
        if p
    )
    situation = (
        f"SITUATION: {intake.incident_description or 'No narrative captured.'}"
        f" When: {intake.incident_datetime or 'unknown'}."
        f" Where: {intake.incident_location or 'unknown'}."
        f" Injuries: {intake.injuries_state or 'unknown'}."
        f" Other parties: {intake.other_parties or 'not stated'}."
        f" Police report: {intake.police_report_filed or 'not stated'}."
    )
    vehicle_line = (
        f"VEHICLE: {vehicle or 'unknown'}."
        f" Drivable: {_yn(intake.is_drivable)}."
        f" Damage: {intake.damage_type or 'unknown'}."
        f" Rebuilt/salvage: {_yn(intake.is_rebuilt_or_salvage)}."
        f" Photos: {intake.photos_available or 'not stated'}."
    )
    insurance = (
        f"insurance via {intake.insurance_provider or 'unknown'}"
        f" (claim: {intake.claim_number or 'pending'})"
        if intake.filing_insurance_claim
        else "private pay"
        if intake.filing_insurance_claim is False
        else "payment path unknown"
    )
    customer = (
        f"CUSTOMER: {intake.caller_name or 'unknown'},"
        f" callback {intake.phone or 'unknown'}, {insurance}."
        f" Readback confirmed: {intake.confirmation_ack or 'not reached'}."
    )
    flags = ", ".join(assessment.flags) or "none"
    action = (
        f"RECOMMENDED ACTION: {assessment.recommended_routing} - "
        f"{assessment.reason} Flags: {flags}."
        f" Missing: {', '.join(assessment.missing_information) or 'none'}."
    )
    return "\n".join([situation, vehicle_line, customer, action])


def _yn(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


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
            "No problem at all. Since the vehicle may not be safe to drive - "
            "or you'd just rather talk with a person - let me get you "
            "straight through to our collision team."
        )
    if disposition == "TRANSFER_GLASS_DEPARTMENT":
        return (
            "That sounds like glass-only damage, and our glass team takes "
            "care of those directly - let me get this over to them for you."
        )
    if disposition == "DECLINED_VEHICLE_YEAR":
        return (
            "I really appreciate you calling. Unfortunately, our collision "
            "centers are only able to take vehicles from 2012 and newer. "
            "Thanks so much for thinking of Birchwood."
        )
    if disposition == "DECLINED_REBUILT_SALVAGE":
        return (
            "Thanks for letting me know. Unfortunately, our collision "
            "centers aren't able to service rebuilt or salvage title "
            "vehicles. I really appreciate you calling Birchwood."
        )
    if "private_pay" in flags:
        return (
            "No problem at all - I've noted the repair as out of pocket. "
            + BIRCHWOOD_COLLISION_NEXT_STEPS_CLOSE
        )
    if "missing_claim_number" in flags:
        return (
            "That's no problem at all - I've noted that the claim number is "
            "still to come, and your advisor will grab it when they call "
            "you back. Thanks so much for calling Birchwood, and take care."
        )
    if disposition == "INCOMPLETE_CALLBACK_NEEDED":
        return (
            "Thanks so much. I've saved everything you've told me, and one "
            "of our service advisors will call you back to fill in the last "
            "couple of details. Thanks for calling Birchwood, and take care."
        )
    if disposition == "HUMAN_REVIEW":
        return (
            "Thank you. I've passed this along for our team to double-check "
            "a few details, and someone will follow up with you shortly. "
            "Thanks for calling Birchwood."
        )
    if "readback_correction" in flags:
        return (
            "Thanks - I've noted that correction for your advisor to "
            "double-check. " + BIRCHWOOD_COLLISION_NEXT_STEPS_CLOSE
        )
    return BIRCHWOOD_COLLISION_NEXT_STEPS_CLOSE


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
    lowered = cleaned.lower()
    if lowered in {"no", "none", "not yet", "no claim number", "n/a"}:
        return None
    # Spoken non-answers ("I don't have it yet") are not claim numbers.
    if re.search(
        r"don'?t have|do not have|not yet|haven'?t|no number|nothing yet",
        lowered,
    ):
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
