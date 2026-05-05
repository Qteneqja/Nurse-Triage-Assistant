"""Baseline healthcare post-call extraction."""

from __future__ import annotations

from typing import Any

from src.platform.extraction.base import BaseExtractionAgent
from src.platform.extraction.schemas import ExtractionResult
from src.platform.workflows.schemas import WorkflowContext, WorkflowFinalResult


class HealthcareExtractionAgent(BaseExtractionAgent):
    """Deterministic read-only extraction for healthcare triage analytics."""

    schema_version = "healthcare_triage_extraction_v1"

    def extract(
        self,
        transcript: list[dict[str, Any]] | str,
        final_result: WorkflowFinalResult,
        workflow_context: WorkflowContext,
        extraction_schema: dict[str, Any] | None,
    ) -> ExtractionResult:
        structured = final_result.structured_output or {}
        intake_state = structured.get("intake_state") or {}
        rules_triggered = list(final_result.rules_triggered or [])
        red_flags = _red_flags_from_events(final_result.safety_events)
        protocols_used = _protocols_from_audit(final_result.audit_metadata)

        entities = {
            "chief_complaint": intake_state.get("chief_complaint"),
            "age": intake_state.get("caller_age"),
            "sex": intake_state.get("caller_sex"),
            "final_disposition": final_result.final_disposition,
            "red_flags_triggered": red_flags,
            "rules_triggered": rules_triggered,
            "protocols_used": protocols_used,
        }
        metrics = {
            "confidence_score": final_result.confidence_score,
            "escalation_required": final_result.final_disposition
            in {"ER_NOW", "URGENT", "HUMAN_REVIEW"},
            "sbar_available": bool(
                structured.get("sbar_report") or structured.get("sbar")
            ),
            "call_duration": _call_duration(workflow_context.metadata),
            "transcript_turn_count": _transcript_turn_count(transcript),
        }

        flags = []
        if metrics["escalation_required"]:
            flags.append("escalation_required")
        flags.extend(red_flags)

        recommended_actions = []
        if final_result.final_disposition in {"ER_NOW", "URGENT"}:
            recommended_actions.append("clinical_follow_up_required")
        elif final_result.final_disposition == "HUMAN_REVIEW":
            recommended_actions.append("human_review_required")

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
            recommended_actions=_dedupe(recommended_actions),
            confidence_score=final_result.confidence_score,
            raw_model_output={
                "deterministic": True,
                "schema": extraction_schema or {},
            },
        )


def _red_flags_from_events(safety_events: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    for event in safety_events:
        flag = event.get("flag") or event.get("rule_id")
        if flag:
            flags.append(str(flag))
    return _dedupe(flags)


def _protocols_from_audit(audit_metadata: dict[str, Any]) -> list[str]:
    protocols = audit_metadata.get("protocols_used") or audit_metadata.get(
        "protocol_citations"
    )
    if not protocols:
        return []
    if isinstance(protocols, list):
        return [str(item) for item in protocols]
    return [str(protocols)]


def _call_duration(metadata: dict[str, Any]) -> Any:
    return metadata.get("call_duration") or metadata.get("call_duration_seconds")


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
