"""Deterministic extraction for ORCA's Birchwood collision intake demo."""

from __future__ import annotations

from typing import Any

from src.platform.extraction.base import BaseExtractionAgent
from src.platform.extraction.schemas import ExtractionResult
from src.platform.workflows.schemas import WorkflowContext, WorkflowFinalResult


class AutomotiveCollisionExtractionAgent(BaseExtractionAgent):
    """Read-only extraction for Birchwood collision intake analytics."""

    schema_version = "automotive_collision_birchwood_extraction_v1"

    def extract(
        self,
        transcript: list[dict[str, Any]] | str,
        final_result: WorkflowFinalResult,
        workflow_context: WorkflowContext,
        extraction_schema: dict[str, Any] | None,
    ) -> ExtractionResult:
        structured = final_result.structured_output or {}
        record = structured.get("intake_record") or {}
        intake = structured.get("intake") or {}

        entities = {
            "workflow_id": workflow_context.workflow_id,
            "vertical": workflow_context.vertical,
            "powered_by": record.get("powered_by"),
            "client_target": record.get("client_target"),
            "status": record.get("workflow_status") or record.get("status"),
            "caller_name": record.get("caller_name") or intake.get("caller_name"),
            "phone": record.get("phone") or intake.get("phone"),
            "email": record.get("email") or intake.get("email"),
            "address": record.get("address") or intake.get("address"),
            "vehicle_year": record.get("vehicle_year") or intake.get("vehicle_year"),
            "vehicle_make": record.get("vehicle_make") or intake.get("vehicle_make"),
            "vehicle_model": record.get("vehicle_model") or intake.get("vehicle_model"),
            "license_plate": record.get("license_plate") or intake.get("license_plate"),
            "is_drivable": record.get("is_drivable"),
            "damage_type": record.get("damage_type") or intake.get("damage_type"),
            "glass_only": record.get("glass_only"),
            "body_damage": record.get("body_damage"),
            "incident_description": record.get("incident_description")
            or intake.get("incident_description"),
            "filing_insurance_claim": record.get("filing_insurance_claim"),
            "claim_number": record.get("claim_number") or intake.get("claim_number"),
            "private_pay": record.get("private_pay"),
            "preferred_collision_center": record.get("preferred_collision_center"),
            "is_luxury": record.get("is_luxury"),
            "is_vw": record.get("is_vw"),
            "is_rebuilt_or_salvage": record.get("is_rebuilt_or_salvage"),
            "flags": record.get("flags") or [],
            "missing_information": record.get("missing_information") or [],
            "recommended_routing": record.get("recommended_routing")
            or final_result.final_disposition,
            "transfer_department": record.get("transfer_department"),
            "decline_reason": record.get("decline_reason"),
            "callback_needed": record.get("callback_needed"),
            "transcript_summary": record.get("transcript_summary")
            or final_result.summary,
            "disclaimers_given": record.get("disclaimers_given") or [],
            "confidence": record.get("confidence") or final_result.confidence_score,
            "human_review_required": record.get("human_review_required"),
        }

        return ExtractionResult(
            session_id=workflow_context.session_id,
            organization_id=workflow_context.organization_id,
            vertical=workflow_context.vertical,
            workflow_id=workflow_context.workflow_id,
            schema_version=self.schema_version,
            summary=final_result.summary,
            entities={
                key: value for key, value in entities.items() if value is not None
            },
            metrics={
                "confidence_score": final_result.confidence_score,
                "transcript_turn_count": _transcript_turn_count(transcript),
            },
            flags=_dedupe(list(record.get("flags") or [])),
            recommended_actions=_recommended_actions(record, final_result),
            confidence_score=final_result.confidence_score,
            raw_model_output={
                "deterministic": True,
                "schema": extraction_schema or {},
            },
        )


def _recommended_actions(
    record: dict[str, Any],
    final_result: WorkflowFinalResult,
) -> list[str]:
    disposition = record.get("recommended_routing") or final_result.final_disposition
    if disposition == "TRANSFER_COLLISION_CENTER":
        return ["transfer_collision_center"]
    if disposition == "TRANSFER_GLASS_DEPARTMENT":
        return ["transfer_glass_department"]
    if disposition in {"DECLINED_VEHICLE_YEAR", "DECLINED_REBUILT_SALVAGE"}:
        return ["no_staff_action_required"]
    if record.get("callback_needed"):
        return ["staff_callback"]
    if record.get("human_review_required"):
        return ["staff_review"]
    return ["collision_team_review"]


def _transcript_turn_count(transcript: list[dict[str, Any]] | str) -> int:
    if isinstance(transcript, list):
        return len(transcript)
    if not transcript:
        return 0
    return len([line for line in transcript.splitlines() if line.strip()])


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
