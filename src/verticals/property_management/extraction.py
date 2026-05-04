"""Baseline property maintenance post-call extraction."""

from __future__ import annotations

from typing import Any

from src.platform.extraction.base import BaseExtractionAgent
from src.platform.extraction.schemas import ExtractionResult
from src.platform.workflows.schemas import WorkflowContext, WorkflowFinalResult


class PropertyMaintenanceExtractionAgent(BaseExtractionAgent):
    """Deterministic read-only extraction for maintenance work-order analytics."""

    schema_version = "property_maintenance_extraction_v1"

    def extract(
        self,
        transcript: list[dict[str, Any]] | str,
        final_result: WorkflowFinalResult,
        workflow_context: WorkflowContext,
        extraction_schema: dict[str, Any] | None,
    ) -> ExtractionResult:
        structured = final_result.structured_output or {}
        work_order = structured.get("work_order") or {}
        intake = structured.get("intake") or {}

        entities = {
            "issue_type": work_order.get("issue_type") or intake.get("issue_type"),
            "urgency": final_result.final_disposition,
            "property_address": work_order.get("property_address")
            or intake.get("property_address"),
            "unit_number": work_order.get("unit_number") or intake.get("unit_number"),
            "access_permission": work_order.get("access_permission")
            or intake.get("access_permission"),
            "vendor_type": work_order.get("vendor_type"),
            "emergency_flags": _emergency_flags(work_order, final_result),
            "recommended_action": work_order.get("recommended_action"),
            "tenant_sentiment": _tenant_sentiment(transcript),
            "repeat_issue": None,
        }
        metrics = {
            "confidence_score": final_result.confidence_score,
            "transcript_turn_count": _transcript_turn_count(transcript),
        }
        flags = list(work_order.get("safety_flags") or [])
        if final_result.final_disposition == "EMERGENCY":
            flags.append("emergency_maintenance")

        return ExtractionResult(
            session_id=workflow_context.session_id,
            organization_id=workflow_context.organization_id,
            vertical=workflow_context.vertical,
            workflow_id=workflow_context.workflow_id,
            schema_version=self.schema_version,
            summary=final_result.summary,
            entities={k: v for k, v in entities.items() if v is not None},
            metrics={k: v for k, v in metrics.items() if v is not None},
            flags=_dedupe(flags),
            recommended_actions=_dedupe(
                [work_order.get("recommended_action") or "property_manager_review"]
            ),
            confidence_score=final_result.confidence_score,
            raw_model_output={
                "deterministic": True,
                "schema": extraction_schema or {},
            },
        )


def _emergency_flags(
    work_order: dict[str, Any],
    final_result: WorkflowFinalResult,
) -> list[str]:
    if final_result.final_disposition != "EMERGENCY":
        return []
    return list(work_order.get("safety_flags") or final_result.rules_triggered or [])


def _tenant_sentiment(transcript: list[dict[str, Any]] | str) -> str | None:
    text = ""
    if isinstance(transcript, str):
        text = transcript.lower()
    elif isinstance(transcript, list):
        text = " ".join(str(turn.get("text", "")) for turn in transcript).lower()
    if any(word in text for word in ["angry", "furious", "upset", "frustrated"]):
        return "frustrated"
    if any(word in text for word in ["thank", "appreciate"]):
        return "positive"
    return None


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
