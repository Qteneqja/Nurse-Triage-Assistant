"""Base interface for post-call extraction agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.platform.extraction.schemas import ExtractionResult
from src.platform.workflows.schemas import WorkflowContext, WorkflowFinalResult


class BaseExtractionAgent(ABC):
    """Read-only post-call extraction agent.

    Extraction output is analytics-only and must never change workflow
    disposition.
    """

    @abstractmethod
    def extract(
        self,
        transcript: list[dict[str, Any]] | str,
        final_result: WorkflowFinalResult,
        workflow_context: WorkflowContext,
        extraction_schema: dict[str, Any] | None,
    ) -> ExtractionResult:
        """Extract structured analytics from a completed conversation."""
        ...
