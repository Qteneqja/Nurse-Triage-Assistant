"""Baseline insurance FNOL post-call extraction."""

from __future__ import annotations

from typing import Any

from src.platform.extraction.base import BaseExtractionAgent
from src.platform.extraction.schemas import ExtractionResult
from src.platform.workflows.schemas import WorkflowContext, WorkflowFinalResult


class InsuranceClaimsExtractionAgent(BaseExtractionAgent):
    """Deterministic read-only extraction for FNOL analytics."""

    schema_version = "insurance_claims_fnol_extraction_v1"

    def extract(
        self,
        transcript: list[dict[str, Any]] | str,
        final_result: WorkflowFinalResult,
        workflow_context: WorkflowContext,
        extraction_schema: dict[str, Any] | None,
    ) -> ExtractionResult:
        structured = final_result.structured_output or {}
        claim = structured.get("claim_record") or {}
        intake = structured.get("intake") or {}

        entities = {
            "workflow_id": workflow_context.workflow_id,
            "vertical": workflow_context.vertical,
            "claim_type": claim.get("claim_type") or intake.get("claim_type"),
            "caller_name": claim.get("caller_name") or intake.get("caller_name"),
            "callback_number": claim.get("callback_number")
            or intake.get("callback_number"),
            "policy_number": claim.get("policy_number") or intake.get("policy_number"),
            "loss_datetime": claim.get("loss_datetime") or intake.get("loss_datetime"),
            "loss_location": claim.get("loss_location") or intake.get("loss_location"),
            "incident_summary": claim.get("incident_summary")
            or intake.get("incident_summary"),
            "emergency_or_safety_issue": claim.get("emergency_or_safety_issue"),
            "injuries_mentioned": claim.get("injuries_mentioned"),
            "emergency_services_involved": claim.get("emergency_services_involved"),
            "police_or_fire_report": claim.get("police_or_fire_report"),
            "property_secure": claim.get("property_secure"),
            "mitigation_needed": claim.get("mitigation_needed"),
            "documents_available": claim.get("documents_available"),
            "missing_information": claim.get("missing_information") or [],
            "recommended_routing": claim.get("recommended_routing")
            or final_result.final_disposition,
            "confidence": claim.get("confidence") or final_result.confidence_score,
            "human_review_required": claim.get("human_review_required"),
            "disclaimers_given": claim.get("disclaimers_given") or [],
        }
        flags = list(claim.get("safety_flags") or [])
        if final_result.final_disposition == "EMERGENCY_SERVICES_NOW":
            flags.append("emergency_services_now")

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
            flags=_dedupe(flags),
            recommended_actions=_dedupe(
                [claim.get("recommended_action") or "claims_team_review"]
            ),
            confidence_score=final_result.confidence_score,
            raw_model_output={
                "deterministic": True,
                "schema": extraction_schema or {},
            },
        )


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
