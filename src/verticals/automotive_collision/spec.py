"""Birchwood collision intake as a complete WorkflowSpec (PR 3).

The spec is the authoritative DEFINITION of the workflow — greeting, stages,
required/optional fields, extraction entities, completion rules, fallback,
summary templates, dashboard display fields, recommended actions, and the
safety branches that apply. The Birchwood workflow class subclasses
SpecDrivenWorkflow with this spec; its vertical-rich behaviors are also
registered as NAMED hooks so the engine (and any spec file) can reference
them without importing vertical code.
"""

from __future__ import annotations

from typing import Any

from src.platform.workflows.spec import (
    GenericAssessment,
    WorkflowSpec,
    WorkflowStageSpec,
    register_hook,
)
from src.verticals.automotive_collision.constants import (
    AUTOMOTIVE_COLLISION_VERTICAL,
    BIRCHWOOD_COLLISION_CLIENT_TARGET,
    BIRCHWOOD_COLLISION_DISPLAY_NAME,
    BIRCHWOOD_COLLISION_OPTIONAL_FIELDS,
    BIRCHWOOD_COLLISION_POWERED_BY,
    BIRCHWOOD_COLLISION_REQUIRED_FIELDS,
    BIRCHWOOD_COLLISION_STATUS,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
    BIRCHWOOD_COLLISION_WORKFLOW_VERSION,
)
from src.verticals.automotive_collision.prompts import (
    BIRCHWOOD_COLLISION_INTRO,
    BIRCHWOOD_COLLISION_NEXT_STEPS_CLOSE,
)

BIRCHWOOD_RULES_HOOK = "automotive_collision_birchwood_rules_v1"
BIRCHWOOD_NARRATIVE_HOOK = "birchwood_narrative_extractor_v1"
BIRCHWOOD_FIELD_HOOK = "birchwood_field_recorded_v1"
BIRCHWOOD_READBACK_HOOK = "birchwood_readback_v1"
BIRCHWOOD_RECORD_HOOK = "birchwood_record_builder_v1"

_impl = None


def _workflow():
    """Lazily share one workflow instance for hook delegation."""
    global _impl
    if _impl is None:
        from src.verticals.automotive_collision.workflow import (
            BirchwoodCollisionIntakeWorkflow,
        )

        _impl = BirchwoodCollisionIntakeWorkflow()
    return _impl


def _rules_hook(
    fields: dict[str, Any],
    dynamic_text: str,
    spec: WorkflowSpec,
) -> GenericAssessment:
    from src.verticals.automotive_collision.rules import classify_collision_intake
    from src.verticals.automotive_collision.workflow import _intake_from_fields

    intake = _intake_from_fields(fields)
    assessment = classify_collision_intake(intake, dynamic_text=dynamic_text)
    return GenericAssessment(
        outcome=assessment.outcome,
        status=assessment.status,
        reason=assessment.reason,
        flags=list(assessment.flags),
        rules_triggered=list(assessment.rules_triggered),
        missing_information=list(assessment.missing_information),
        callback_needed=assessment.callback_needed,
        human_review_required=assessment.human_review_required,
        confidence=assessment.confidence,
        transfer_department=assessment.transfer_department,
        extra={"assessment": assessment.model_dump(mode="json")},
    )


def _record_hook(context, session, fields, assessment: GenericAssessment) -> dict:
    from src.verticals.automotive_collision.rules import classify_collision_intake
    from src.verticals.automotive_collision.workflow import (
        _intake_from_fields,
        _intake_record,
    )

    intake = _intake_from_fields(fields)
    full_assessment = classify_collision_intake(intake)
    return _intake_record(context, session, intake, full_assessment).model_dump(
        mode="json"
    )


register_hook("completion_rules", BIRCHWOOD_RULES_HOOK, _rules_hook)
register_hook("record_builder", BIRCHWOOD_RECORD_HOOK, _record_hook)
register_hook(
    "narrative_extractor",
    BIRCHWOOD_NARRATIVE_HOOK,
    lambda session, stage, narrative: _workflow().prefill_from_narrative(
        session, stage, narrative
    ),
)
register_hook(
    "field_recorded_hook",
    BIRCHWOOD_FIELD_HOOK,
    lambda session, stage, value: _workflow().on_field_recorded(session, stage, value),
)
register_hook(
    "dynamic_prompt_builder",
    BIRCHWOOD_READBACK_HOOK,
    lambda session, stage: _workflow().build_dynamic_prompt(session, stage),
)


def build_birchwood_spec() -> WorkflowSpec:
    from src.verticals.automotive_collision.workflow import (
        BIRCHWOOD_EXTRACTION_ENTITIES,
        _scripted_stage_definitions,
    )

    stages = [
        WorkflowStageSpec(
            stage_id=stage.stage_id,
            field_name=stage.field_name,
            prompt=stage.prompt,
            field_type=stage.field_type or "free_text",
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
        for stage in _scripted_stage_definitions()
    ]
    return WorkflowSpec(
        workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
        vertical=AUTOMOTIVE_COLLISION_VERTICAL,
        version=BIRCHWOOD_COLLISION_WORKFLOW_VERSION,
        display_name=BIRCHWOOD_COLLISION_DISPLAY_NAME,
        greeting=BIRCHWOOD_COLLISION_INTRO,
        stages=stages,
        required_fields=list(BIRCHWOOD_COLLISION_REQUIRED_FIELDS),
        optional_fields=list(BIRCHWOOD_COLLISION_OPTIONAL_FIELDS),
        extraction_entities=list(BIRCHWOOD_EXTRACTION_ENTITIES),
        completion_rules=BIRCHWOOD_RULES_HOOK,
        summary_templates={
            "caller": (
                "We recorded your collision details. The Birchwood team "
                "will review and call you back. This doesn't confirm "
                "coverage, pricing, or an appointment yet."
            ),
            "business": ("{outcome}: {reason} Missing: {missing}. Flags: {flags}."),
        },
        fallback_route={
            "action": "callback",
            "message": (
                "I've saved what you've told me so far, and someone from "
                "the team will call you back shortly."
            ),
        },
        dashboard_display_fields=[
            "caller_name",
            "phone",
            "vehicle_year",
            "vehicle_make",
            "vehicle_model",
            "is_drivable",
            "damage_type",
            "incident_datetime",
            "incident_location",
            "injuries_state",
            "filing_insurance_claim",
            "insurance_provider",
            "claim_number",
            "recommended_routing",
            "flags",
            "human_review_required",
        ],
        recommended_actions={
            "COMPLETED_INTAKE": (
                "Review the intake record and call the customer back to "
                "schedule the repair."
            ),
            "INCOMPLETE_CALLBACK_NEEDED": (
                "Call the customer back to collect the missing information."
            ),
            "TRANSFER_COLLISION_CENTER": (
                "Caller was routed to the collision team — confirm tow/"
                "pickup and next steps."
            ),
            "TRANSFER_GLASS_DEPARTMENT": (
                "Glass-only damage — glass department follows up."
            ),
            "DECLINED_VEHICLE_YEAR": "No action — declined per policy (2011 or older).",
            "DECLINED_REBUILT_SALVAGE": (
                "No action — declined per policy (rebuilt/salvage title)."
            ),
            "ESCALATE_SAFETY": (
                "Safety/legal escalation — connect the caller to a human "
                "immediately; do not handle as routine intake."
            ),
            "HUMAN_REVIEW": "Staff review required before contacting the customer.",
        },
        final_messages={"COMPLETED_INTAKE": BIRCHWOOD_COLLISION_NEXT_STEPS_CLOSE},
        safety_hooks=[
            "injury_safety_branch",
            "safety_escalation_branch",
            "restricted_advice_boundary",
        ],
        narrative_extractor=BIRCHWOOD_NARRATIVE_HOOK,
        field_recorded_hook=BIRCHWOOD_FIELD_HOOK,
        dynamic_prompt_builder=BIRCHWOOD_READBACK_HOOK,
        record_builder=BIRCHWOOD_RECORD_HOOK,
        metadata={
            "powered_by": BIRCHWOOD_COLLISION_POWERED_BY,
            "client_target": BIRCHWOOD_COLLISION_CLIENT_TARGET,
            "status": BIRCHWOOD_COLLISION_STATUS,
        },
    )
