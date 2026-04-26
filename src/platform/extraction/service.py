"""Post-call extraction orchestration."""

from __future__ import annotations

import logging
from typing import Any

from src.platform.extraction.base import BaseExtractionAgent
from src.platform.extraction.schemas import ExtractionResult
from src.platform.workflows.registry import ensure_default_workflows_registered
from src.platform.workflows.schemas import WorkflowContext, WorkflowFinalResult

logger = logging.getLogger(__name__)


class NoopExtractionAgent(BaseExtractionAgent):
    """Safe fallback extractor for workflows without extraction support."""

    def extract(
        self,
        transcript: list[dict[str, Any]] | str,
        final_result: WorkflowFinalResult,
        workflow_context: WorkflowContext,
        extraction_schema: dict[str, Any] | None,
    ) -> ExtractionResult:
        return ExtractionResult(
            session_id=workflow_context.session_id,
            organization_id=workflow_context.organization_id,
            vertical=workflow_context.vertical,
            workflow_id=workflow_context.workflow_id,
            schema_version="noop_v1",
            summary=final_result.summary,
            entities={},
            metrics={},
            flags=[],
            recommended_actions=[],
            confidence_score=0.0,
        )


class ExtractionService:
    """Registry and execution service for post-call extraction."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseExtractionAgent] = {}
        self._noop = NoopExtractionAgent()

    def register(self, workflow_id: str, agent: BaseExtractionAgent) -> None:
        self._agents[workflow_id] = agent

    def extract(
        self,
        transcript: list[dict[str, Any]] | str,
        final_result: WorkflowFinalResult,
        workflow_context: WorkflowContext,
        extraction_schema: dict[str, Any] | None = None,
    ) -> ExtractionResult:
        agent = self._agents.get(workflow_context.workflow_id, self._noop)
        before_disposition = final_result.final_disposition
        result = agent.extract(
            transcript=transcript,
            final_result=final_result,
            workflow_context=workflow_context,
            extraction_schema=extraction_schema,
        )
        if final_result.final_disposition != before_disposition:
            final_result.final_disposition = before_disposition
            raise RuntimeError("Extraction attempted to mutate final disposition")
        return result

    def extract_and_persist(
        self,
        transcript: list[dict[str, Any]] | str,
        final_result: WorkflowFinalResult,
        workflow_context: WorkflowContext,
        extraction_schema: dict[str, Any] | None = None,
    ) -> ExtractionResult:
        before_disposition = final_result.final_disposition
        try:
            result = self.extract(
                transcript=transcript,
                final_result=final_result,
                workflow_context=workflow_context,
                extraction_schema=extraction_schema,
            )
        except Exception as exc:
            final_result.final_disposition = before_disposition
            logger.warning(
                "[Extraction] Extraction failed for session %s workflow %s: %s",
                workflow_context.session_id,
                workflow_context.workflow_id,
                type(exc).__name__,
            )
            result = ExtractionResult(
                session_id=workflow_context.session_id,
                organization_id=workflow_context.organization_id,
                vertical=workflow_context.vertical,
                workflow_id=workflow_context.workflow_id,
                schema_version="extraction_failed_v1",
                summary=final_result.summary,
                entities={"final_disposition": before_disposition},
                metrics={},
                flags=["extraction_failed"],
                recommended_actions=[],
                confidence_score=0.0,
                raw_model_output={"error_type": type(exc).__name__},
            )
        try:
            from src.storage.session_repository import get_session_repository

            get_session_repository().persist_extraction(result)
        except Exception as exc:
            logger.warning(
                "[Extraction] Failed to persist extraction for session %s: %s",
                workflow_context.session_id,
                type(exc).__name__,
            )
        return result


_service: ExtractionService | None = None
_defaults_registered = False


def get_extraction_service() -> ExtractionService:
    global _service, _defaults_registered
    if _service is None:
        _service = ExtractionService()
    if not _defaults_registered:
        _register_default_agents(_service)
        _defaults_registered = True
    return _service


def reset_extraction_service() -> None:
    global _service, _defaults_registered
    _service = None
    _defaults_registered = False


def _register_default_agents(service: ExtractionService) -> None:
    ensure_default_workflows_registered()
    from src.verticals.healthcare.constants import HEALTHCARE_TRIAGE_WORKFLOW_ID
    from src.verticals.healthcare.extraction import HealthcareExtractionAgent

    service.register(HEALTHCARE_TRIAGE_WORKFLOW_ID, HealthcareExtractionAgent())
