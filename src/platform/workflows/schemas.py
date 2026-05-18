"""Generic workflow data contracts.

These schemas are intentionally vertical-neutral. Healthcare, property
management, sales, support, and future workflows should all be able to express
their turn/final results through these platform contracts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class WorkflowDefinition(BaseModel):
    """Static metadata for a workflow implementation."""

    workflow_id: str
    vertical: str
    version: str
    display_name: str
    required_fields: list[str] = Field(default_factory=list)
    supported_output_types: list[str] = Field(default_factory=list)
    default_output_type: str
    supports_post_call_extraction: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScriptedStageDefinition(BaseModel):
    """Vertical-provided scripted intake stage definition."""

    stage_id: str
    field_name: str
    prompt: str
    prompt_text: str | None = None
    reprompt_text: str | None = None
    expected_answer_type: str = "free_text"
    field_type: str | None = None
    required: bool = True
    allowed_values: list[str] | None = None
    max_attempts: int | None = Field(default=None, ge=1)
    store_as: str | None = None
    sensitivity: str | None = None
    validation_hint: str | None = None
    hints: str | None = None
    speech_profile: str | None = None
    timeout_seconds: int = 8
    speech_timeout: str = "3"

    @model_validator(mode="after")
    def _sync_legacy_and_generic_fields(self) -> "ScriptedStageDefinition":
        """Keep Phase 10 names and Phase 11 names interchangeable."""
        if self.prompt_text is None:
            self.prompt_text = self.prompt
        if self.field_type is None:
            self.field_type = self.expected_answer_type
        if self.store_as is None:
            self.store_as = self.field_name
        return self


class ScriptedIntakeDefinition(BaseModel):
    """Ordered scripted intake definition for voice channels."""

    stages: list[ScriptedStageDefinition] = Field(default_factory=list)
    intro_text: str | None = None
    completion_stage_id: str = "DYNAMIC"


class WorkflowContext(BaseModel):
    """Runtime context resolved before a workflow turn is executed."""

    session_id: str
    organization_id: str | None = None
    phone_number_id: str | None = None
    call_sid: str | None = None
    vertical: str
    workflow_id: str
    workflow_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    correlation_id: str | None = None


class WorkflowInput(BaseModel):
    """One caller/user input plus the workflow's current serialized state."""

    user_text: str
    session_state: dict[str, Any] = Field(default_factory=dict)
    caller_phone_number: str | None = None
    called_phone_number: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowTurnResult(BaseModel):
    """Result of one workflow turn."""

    assistant_text: str
    stage: str
    should_continue: bool
    should_finalize: bool
    escalation_required: bool
    recommended_disposition: str | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    rules_triggered: list[str] = Field(default_factory=list)
    safety_events: list[dict[str, Any]] = Field(default_factory=list)
    updated_state: dict[str, Any] = Field(default_factory=dict)
    audit_metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowFinalResult(BaseModel):
    """Final workflow output, suitable for storage and extraction."""

    final_disposition: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    summary: str
    structured_output: dict[str, Any] = Field(default_factory=dict)
    safety_events: list[dict[str, Any]] = Field(default_factory=list)
    rules_triggered: list[str] = Field(default_factory=list)
    audit_metadata: dict[str, Any] = Field(default_factory=dict)


class ResolvedWorkflowRoute(BaseModel):
    """Resolved routing decision for an incoming channel event."""

    organization_id: str | None = None
    organization_name: str | None = None
    vertical_key: str
    workflow_id: str
    workflow_version: str
    phone_number_id: str | None = None
    config_json: dict[str, Any] = Field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str | None = None
    safe_response_required: bool = False
    audit_metadata: dict[str, Any] = Field(default_factory=dict)
