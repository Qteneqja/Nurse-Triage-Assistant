"""Declarative workflow specification (PR 3).

A ``WorkflowSpec`` fully describes a voice intake workflow as DATA: greeting,
scripted stages, required/optional fields, extraction entities, completion
rules, fallback route, summary templates, dashboard display fields, and
recommended actions. ``SpecDrivenWorkflow`` (spec_workflow.py) interprets a
spec generically; vertical-specific behavior plugs in through NAMED hook
callables registered in the hook registry — never by overriding the engine.

Safety is NOT configurable from a spec. The injury safety branch for
non-clinical verticals and the entire healthcare safety stack are enforced
beneath the workflow layer (engine overlay + reserved IDs); ``safety_hooks``
in a spec is documentation, not a switch.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

WORKFLOW_SPEC_VERSION = "workflow_spec_v1"

# Workflow IDs that a spec may NEVER claim or replace: the healthcare stack
# stays on its proven code path for the pilot (engine migration is a
# post-pilot phase with its own safety re-validation).
RESERVED_WORKFLOW_IDS = frozenset({"healthcare_triage_v1"})
RESERVED_VERTICALS = frozenset({"healthcare"})


class WorkflowStageSpec(BaseModel):
    """One scripted stage, declaratively."""

    stage_id: str
    field_name: str
    prompt: str
    field_type: str = "free_text"
    required: bool = True
    reprompt_text: str | None = None
    allowed_values: list[str] | None = None
    max_attempts: int | None = Field(default=None, ge=1)
    sensitivity: str | None = None
    hints: str | None = None
    speech_profile: str | None = None
    timeout_seconds: int = 8
    speech_timeout: str = "3"
    multi_segment: bool = False
    dynamic_prompt: bool = False


class FallbackRouteSpec(BaseModel):
    """What happens when the workflow cannot complete normally."""

    action: str = "callback"  # "callback" | "transfer"
    message: str = (
        "I've saved what we have so far, and the team will call you back "
        "to finish up. Thanks for calling."
    )
    transfer_department: str | None = None


class SummaryTemplatesSpec(BaseModel):
    """Final summary templates. ``{field}`` placeholders substitute from the
    captured fields plus ``{outcome}``/``{reason}``; missing values render as
    'unknown' (safe substitution — templates can never crash a call)."""

    caller: str = (
        "We recorded your details. The team will review and follow up. "
        "This doesn't confirm pricing or an appointment yet."
    )
    business: str = "Outcome: {outcome}. Reason: {reason}."


class WorkflowSpec(BaseModel):
    """Complete declarative definition of a workflow."""

    spec_version: str = WORKFLOW_SPEC_VERSION
    workflow_id: str
    vertical: str
    version: str = "v1"
    display_name: str
    greeting: str
    stages: list[WorkflowStageSpec] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    optional_fields: list[str] = Field(default_factory=list)
    extraction_entities: list[str] = Field(default_factory=list)
    # Name of a registered completion-rules callable. The default engine
    # marks the intake complete when every required field is present.
    completion_rules: str = "default_required_fields_v1"
    fallback_route: FallbackRouteSpec = Field(default_factory=FallbackRouteSpec)
    summary_templates: SummaryTemplatesSpec = Field(
        default_factory=SummaryTemplatesSpec
    )
    dashboard_display_fields: list[str] = Field(default_factory=list)
    # outcome -> recommended next action shown to the business/dashboard.
    recommended_actions: dict[str, str] = Field(default_factory=dict)
    # outcome -> spoken closing message override (engine may prepend safety
    # advisories; specs cannot prevent that).
    final_messages: dict[str, str] = Field(default_factory=dict)
    # Documentation of which hard-wired safety branches apply. Informational:
    # the engine enforces them regardless of what a spec declares.
    safety_hooks: list[str] = Field(default_factory=list)
    # Optional named hooks (resolved from the hook registry at build time).
    narrative_extractor: str | None = None
    field_recorded_hook: str | None = None
    dynamic_prompt_builder: str | None = None
    record_builder: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("workflow_id")
    @classmethod
    def _reject_reserved_ids(cls, value: str) -> str:
        if value in RESERVED_WORKFLOW_IDS:
            raise ValueError(
                f"workflow_id '{value}' is reserved — the healthcare stack "
                "cannot be replaced by a spec (post-pilot migration only)."
            )
        return value

    @field_validator("vertical")
    @classmethod
    def _reject_reserved_verticals(cls, value: str) -> str:
        if value in RESERVED_VERTICALS:
            raise ValueError(
                f"vertical '{value}' is reserved — specs cannot define "
                "healthcare workflows."
            )
        return value


class GenericAssessment(BaseModel):
    """Vertical-neutral completion-rules output."""

    outcome: str
    status: str = "completed"
    reason: str = ""
    flags: list[str] = Field(default_factory=list)
    rules_triggered: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    callback_needed: bool = False
    human_review_required: bool = False
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    transfer_department: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Hook registry — named callables a spec may reference. Registration happens
# in vertical modules at import time; specs refer to hooks by name only, so a
# definition FILE can never inject code.
# ---------------------------------------------------------------------------

_HOOKS: dict[str, dict[str, Callable[..., Any]]] = {
    "completion_rules": {},
    "narrative_extractor": {},
    "field_recorded_hook": {},
    "dynamic_prompt_builder": {},
    "record_builder": {},
}


class UnknownHookError(KeyError):
    """A spec referenced a hook name that is not registered (fail closed)."""


def register_hook(kind: str, name: str, fn: Callable[..., Any]) -> None:
    if kind not in _HOOKS:
        raise ValueError(f"Unknown hook kind '{kind}'")
    _HOOKS[kind][name] = fn


def resolve_hook(kind: str, name: str | None) -> Callable[..., Any] | None:
    if name is None:
        return None
    try:
        return _HOOKS[kind][name]
    except KeyError as exc:
        raise UnknownHookError(
            f"Spec references unregistered {kind} hook '{name}'"
        ) from exc


def default_required_fields_rules(
    fields: dict[str, Any],
    dynamic_text: str,
    spec: WorkflowSpec,
) -> GenericAssessment:
    """Default completion rules: complete iff every required field present."""
    missing = [
        name for name in spec.required_fields if fields.get(name) in (None, "", [])
    ]
    if missing:
        return GenericAssessment(
            outcome="INCOMPLETE_CALLBACK_NEEDED",
            status="incomplete",
            reason="Required information is missing.",
            flags=["callback_needed"],
            rules_triggered=[f"{spec.workflow_id}:incomplete_callback_needed"],
            missing_information=missing,
            callback_needed=True,
            human_review_required=True,
            confidence=0.6,
        )
    return GenericAssessment(
        outcome="COMPLETED_INTAKE",
        status="completed",
        reason="All required fields were captured.",
        rules_triggered=[f"{spec.workflow_id}:completed_intake"],
        confidence=0.85,
    )


register_hook(
    "completion_rules",
    "default_required_fields_v1",
    default_required_fields_rules,
)
