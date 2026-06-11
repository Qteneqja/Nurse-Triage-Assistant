"""Abstract base class for pluggable decision-support workflows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.platform.workflows.schemas import (
    ScriptedIntakeDefinition,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowFinalResult,
    WorkflowInput,
    WorkflowTurnResult,
)


class BaseWorkflow(ABC):
    """Common contract implemented by every vertical workflow."""

    @abstractmethod
    def get_definition(self) -> WorkflowDefinition:
        """Return static workflow metadata."""
        ...

    @abstractmethod
    def start_session(self, context: WorkflowContext) -> dict[str, Any]:
        """Return initial workflow state for a new session."""
        ...

    @abstractmethod
    async def handle_turn(
        self,
        context: WorkflowContext,
        input: WorkflowInput,
    ) -> WorkflowTurnResult:
        """Process one user/caller turn."""
        ...

    @abstractmethod
    async def finalize(
        self,
        context: WorkflowContext,
        session_state: dict[str, Any],
    ) -> WorkflowFinalResult:
        """Finalize a session and return the vertical's structured output."""
        ...

    def get_extraction_schema(self) -> dict[str, Any] | None:
        """Return a vertical-specific extraction schema, if supported."""
        return None

    def get_scripted_intake_definition(self) -> ScriptedIntakeDefinition | None:
        """Return optional voice scripted-intake stages for this workflow."""
        return None

    def prefill_from_narrative(
        self,
        session: Any,
        stage: Any,
        narrative: str,
    ) -> dict[str, Any]:
        """Extract field values from a completed narrative answer.

        Returned keys are recorded into the scripted-intake fields (without
        overwriting values already present) so later stages can be skipped.
        Deterministic only — implementations must not call an LLM here.
        """
        return {}

    def on_field_recorded(
        self,
        session: Any,
        stage: Any,
        value: Any,
    ) -> dict[str, Any]:
        """React to a recorded scripted field with additional prefills.

        Lets a workflow resolve conditional stages deterministically (e.g.
        'paying privately' also answers the claim-number question).
        """
        return {}

    def build_dynamic_prompt(self, session: Any, stage: Any) -> str | None:
        """Build a session-aware prompt for stages marked dynamic_prompt."""
        return None
