"""Workflow routing and execution helpers."""

from __future__ import annotations

import logging
import re
from typing import Protocol

import src.config as config
from src.platform.workflows.registry import ensure_default_workflows_registered
from src.platform.workflows.schemas import (
    ResolvedWorkflowRoute,
    WorkflowContext,
    WorkflowFinalResult,
    WorkflowInput,
    WorkflowTurnResult,
)
from src.safety.phi_masking import mask_phi

logger = logging.getLogger(__name__)


class RoutingRepository(Protocol):
    """Minimal repository contract used by the route resolver."""

    def resolve_active_phone_number(
        self, e164_number: str
    ) -> ResolvedWorkflowRoute | None: ...


class WorkflowRouteResolver:
    """Resolve incoming calls to organization, vertical, and workflow."""

    def __init__(self, repository: RoutingRepository | None = None) -> None:
        self._repository = repository

    def resolve(
        self,
        called_phone_number: str | None,
        organization_hint: str | None = None,
        workflow_hint: str | None = None,
    ) -> ResolvedWorkflowRoute:
        normalized = _normalize_phone(called_phone_number)
        if normalized and self._repository is not None:
            try:
                route = self._repository.resolve_active_phone_number(normalized)
                if route is not None:
                    route.audit_metadata.setdefault("routing_source", "phone_number")
                    route.audit_metadata.setdefault("phone_route_found", True)
                    return route
            except Exception as exc:
                logger.warning(
                    "[Routing] Phone route lookup failed for %s: %s",
                    _mask_phone(normalized),
                    type(exc).__name__,
                )

        configured_route = _configured_phone_number_route(normalized)
        if configured_route is not None:
            return configured_route

        if workflow_hint and _workflow_hint_route_enabled():
            return _default_route(
                workflow_id=workflow_hint,
                fallback_reason="workflow_hint",
                called_phone_number=normalized,
                audit_extra={"organization_hint": organization_hint},
            )
        if workflow_hint:
            logger.warning(
                "[Routing] Ignoring workflow hint because hint routing is disabled",
            )

        if _default_workflow_route_enabled():
            logger.warning(
                "[Routing] Falling back to default workflow for called number %s",
                _mask_phone(normalized),
            )
            return _default_route(
                fallback_reason=(
                    "missing_phone_number_route"
                    if normalized
                    else "missing_called_phone_number"
                ),
                called_phone_number=normalized,
            )

        logger.error(
            "[Routing] No active route and default fallback disabled for %s",
            _mask_phone(normalized),
        )
        return _default_route(
            fallback_reason="routing_missing_default_disabled",
            called_phone_number=normalized,
            safe_response_required=True,
        )


class WorkflowEngine:
    """Dispatcher from route/context to a registered workflow.

    The engine applies the platform safety overlay AFTER every workflow
    turn/finalize: the injury safety branch for non-clinical verticals is
    hard-wired here, beneath the workflow layer, so no workflow definition
    (malicious, buggy, or misconfigured) can disable or weaken it.
    Healthcare results pass through untouched — its safety stack lives in
    the orchestrator and is not reachable from workflow definitions at all
    (reserved IDs).
    """

    async def handle_turn(
        self,
        context: WorkflowContext,
        workflow_input: WorkflowInput,
    ) -> WorkflowTurnResult:
        registry = ensure_default_workflows_registered()
        workflow = registry.get(context.workflow_id)
        result = await workflow.handle_turn(context, workflow_input)
        return _enforce_turn_safety_overlay(context, workflow_input, result)

    async def finalize(
        self,
        context: WorkflowContext,
        session_state: dict,
    ) -> WorkflowFinalResult:
        registry = ensure_default_workflows_registered()
        workflow = registry.get(context.workflow_id)
        result = await workflow.finalize(context, session_state)
        return _enforce_final_safety_overlay(context, session_state, result)


PLATFORM_INJURY_RULE_ID = "platform:injury_safety_branch"


def _caller_text_for_safety_scan(
    workflow_input_text: str,
    session_state: dict,
) -> str:
    """Caller-authored text from this turn plus the stored conversation."""
    parts = [workflow_input_text or ""]
    conversation = (session_state or {}).get("conversation") or []
    for turn in conversation:
        if isinstance(turn, dict) and turn.get("role") == "caller":
            parts.append(str(turn.get("text") or ""))
    scripted = ((session_state or {}).get("channel_metadata") or {}).get(
        "scripted_intake"
    ) or {}
    for value in (scripted.get("fields") or {}).values():
        if isinstance(value, str):
            parts.append(value)
    return " ".join(p for p in parts if p)


def _enforce_turn_safety_overlay(
    context: WorkflowContext,
    workflow_input: WorkflowInput,
    result: WorkflowTurnResult,
) -> WorkflowTurnResult:
    if context.vertical == "healthcare":
        return result
    from src.safety.injury_detection import (
        INJURY_SAFETY_ADVISORY,
        scan_for_injuries,
    )

    text = _caller_text_for_safety_scan(workflow_input.user_text, result.updated_state)
    scan = scan_for_injuries(text)
    if not scan.mentioned:
        return result

    record = (
        (result.updated_state.get("channel_metadata") or {})
        .get("workflow_final_result", {})
        .get("structured_output", {})
        .get("intake_record")
    )
    if isinstance(record, dict):
        flags = list(record.get("flags") or [])
        if "injuries_reported" not in flags:
            record["flags"] = ["injuries_reported", *flags]
        record["human_review_required"] = True
    if PLATFORM_INJURY_RULE_ID not in result.rules_triggered:
        result.rules_triggered = [*result.rules_triggered, PLATFORM_INJURY_RULE_ID]
        result.safety_events = [
            *result.safety_events,
            {"rule_id": PLATFORM_INJURY_RULE_ID, "flag": "injuries_reported"},
        ]
    # Speak the advisory at most once per call: the voice channel marks
    # webhook_stability.injury_advisory_given when it has already been said.
    stability_state = (result.updated_state.get("channel_metadata") or {}).setdefault(
        "webhook_stability", {}
    )
    if (
        not stability_state.get("injury_advisory_given")
        and "9 1 1" not in result.assistant_text
    ):
        result.assistant_text = f"{INJURY_SAFETY_ADVISORY} {result.assistant_text}"
        stability_state["injury_advisory_given"] = True
    return result


def _enforce_final_safety_overlay(
    context: WorkflowContext,
    session_state: dict,
    result: WorkflowFinalResult,
) -> WorkflowFinalResult:
    if context.vertical == "healthcare":
        return result
    from src.safety.injury_detection import scan_for_injuries

    text = _caller_text_for_safety_scan("", session_state)
    scan = scan_for_injuries(text)
    if not scan.mentioned:
        return result
    record = result.structured_output.get("intake_record")
    if isinstance(record, dict):
        flags = list(record.get("flags") or [])
        if "injuries_reported" not in flags:
            record["flags"] = ["injuries_reported", *flags]
        record["human_review_required"] = True
    if PLATFORM_INJURY_RULE_ID not in result.rules_triggered:
        result.rules_triggered = [*result.rules_triggered, PLATFORM_INJURY_RULE_ID]
        result.safety_events = [
            *result.safety_events,
            {"rule_id": PLATFORM_INJURY_RULE_ID, "flag": "injuries_reported"},
        ]
    return result


_route_resolver: WorkflowRouteResolver | None = None
_workflow_engine: WorkflowEngine | None = None


def get_workflow_route_resolver() -> WorkflowRouteResolver:
    """Return a resolver using the SQL repository when Postgres is active."""

    global _route_resolver
    if _route_resolver is None:
        _route_resolver = WorkflowRouteResolver(repository=_build_repository())
    return _route_resolver


def reset_workflow_route_resolver() -> None:
    """Reset the route resolver singleton. Intended for tests."""

    global _route_resolver
    _route_resolver = None


def get_workflow_engine() -> WorkflowEngine:
    global _workflow_engine
    if _workflow_engine is None:
        _workflow_engine = WorkflowEngine()
    return _workflow_engine


def _build_repository() -> RoutingRepository | None:
    try:
        from src.storage.factory import get_storage_backend

        backend = get_storage_backend()
        session_factory = getattr(backend, "_SessionFactory", None)
        if session_factory is None:
            return None
        from src.platform.organizations.repository import OrganizationRepository

        return OrganizationRepository(session_factory=session_factory)
    except Exception as exc:
        logger.warning("[Routing] Repository unavailable: %s", type(exc).__name__)
        return None


def _default_route(
    *,
    workflow_id: str | None = None,
    fallback_reason: str,
    called_phone_number: str | None = None,
    safe_response_required: bool = False,
    audit_extra: dict | None = None,
) -> ResolvedWorkflowRoute:
    workflow_id = workflow_id or getattr(config, "DEFAULT_WORKFLOW_ID", "")
    vertical_key = getattr(config, "DEFAULT_VERTICAL_KEY", "")
    workflow_version = getattr(config, "DEFAULT_WORKFLOW_VERSION", "v1")
    return ResolvedWorkflowRoute(
        vertical_key=vertical_key,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        config_json={},
        fallback_used=True,
        fallback_reason=fallback_reason,
        safe_response_required=safe_response_required,
        audit_metadata={
            "routing_source": "default_fallback",
            "fallback_reason": fallback_reason,
            "called_phone_number_masked": _mask_phone(called_phone_number),
            "environment": getattr(config, "ENVIRONMENT", "development"),
            "default_route_enabled": _default_workflow_route_enabled(),
            **(audit_extra or {}),
        },
    )


def _default_workflow_route_enabled() -> bool:
    if getattr(config, "ENVIRONMENT", "development") == "production":
        return bool(getattr(config, "ENABLE_DEFAULT_WORKFLOW_ROUTE", False))
    return getattr(config, "ENABLE_DEFAULT_WORKFLOW_ROUTE", True)


def _workflow_hint_route_enabled() -> bool:
    if getattr(config, "ENVIRONMENT", "development") == "production":
        return bool(getattr(config, "ENABLE_WORKFLOW_HINT_ROUTE", False))
    return getattr(config, "ENABLE_WORKFLOW_HINT_ROUTE", True)


def _configured_phone_number_route(
    called_phone_number: str | None,
) -> ResolvedWorkflowRoute | None:
    if not called_phone_number:
        return None

    # Generic config-driven routing (PR 3): WORKFLOW_PHONE_ROUTES maps an
    # E.164 number to any registered workflow_id — adding a vertical needs
    # config + a registered workflow, never core code changes.
    generic_route = _generic_configured_workflow_route(called_phone_number)
    if generic_route is not None:
        return generic_route

    insurance_route = _insurance_phone_number_route(called_phone_number)
    if insurance_route is not None:
        return insurance_route

    return _birchwood_collision_phone_number_route(called_phone_number)


def _generic_configured_workflow_route(
    called_phone_number: str,
) -> ResolvedWorkflowRoute | None:
    routes = getattr(config, "WORKFLOW_PHONE_ROUTES", None) or {}
    workflow_id = None
    for number, candidate in routes.items():
        if _normalize_phone(number) == called_phone_number:
            workflow_id = str(candidate)
            break
    if workflow_id is None:
        return None
    try:
        definition = (
            ensure_default_workflows_registered().get(workflow_id).get_definition()
        )
    except Exception:
        logger.error(
            "[Routing] WORKFLOW_PHONE_ROUTES references unknown workflow "
            "'%s' — ignoring the route",
            workflow_id,
        )
        return None
    return ResolvedWorkflowRoute(
        vertical_key=definition.vertical,
        workflow_id=definition.workflow_id,
        workflow_version=definition.version,
        config_json={"route_type": "workflow_phone_routes"},
        fallback_used=False,
        audit_metadata={
            "routing_source": "configured_phone_number",
            "configured_key": "WORKFLOW_PHONE_ROUTES",
            "called_phone_number_masked": _mask_phone(called_phone_number),
        },
    )


def _insurance_phone_number_route(
    called_phone_number: str,
) -> ResolvedWorkflowRoute | None:
    configured = _normalize_phone(getattr(config, "INSURANCE_FNOL_PHONE_NUMBER", ""))
    if not configured or called_phone_number != configured:
        return None

    from src.verticals.insurance.constants import (
        INSURANCE_CLAIMS_FNOL_WORKFLOW_ID,
        INSURANCE_CLAIMS_FNOL_WORKFLOW_VERSION,
        INSURANCE_VERTICAL,
    )

    return ResolvedWorkflowRoute(
        vertical_key=INSURANCE_VERTICAL,
        workflow_id=INSURANCE_CLAIMS_FNOL_WORKFLOW_ID,
        workflow_version=INSURANCE_CLAIMS_FNOL_WORKFLOW_VERSION,
        config_json={"route_type": "insurance_fnol_placeholder"},
        fallback_used=False,
        audit_metadata={
            "routing_source": "configured_phone_number",
            "configured_key": "INSURANCE_FNOL_PHONE_NUMBER",
            "called_phone_number_masked": _mask_phone(called_phone_number),
            "environment": getattr(config, "ENVIRONMENT", "development"),
        },
    )


def _birchwood_collision_phone_number_route(
    called_phone_number: str,
) -> ResolvedWorkflowRoute | None:
    configured = _normalize_phone(
        getattr(config, "BIRCHWOOD_COLLISION_PHONE_NUMBER", "")
    )
    if not configured or called_phone_number != configured:
        return None

    from src.verticals.automotive_collision.constants import (
        AUTOMOTIVE_COLLISION_VERTICAL,
        BIRCHWOOD_COLLISION_CLIENT_TARGET,
        BIRCHWOOD_COLLISION_DISPLAY_NAME,
        BIRCHWOOD_COLLISION_POWERED_BY,
        BIRCHWOOD_COLLISION_STATUS,
        BIRCHWOOD_COLLISION_WORKFLOW_ID as LIVE_WORKFLOW_ID,
        BIRCHWOOD_COLLISION_WORKFLOW_VERSION as LIVE_WORKFLOW_VERSION,
    )

    # Which collision workflow this number uses. The default comes from
    # config.BIRCHWOOD_COLLISION_WORKFLOW_ID (now birchwood_collision_intake_v1,
    # the live pilot id == LIVE_WORKFLOW_ID); set it to
    # birchwood_collision_intake_min_v1 for the minimal-only package. The
    # `or LIVE_WORKFLOW_ID` guard only applies if the env var is explicitly empty.
    target_id = (
        getattr(config, "BIRCHWOOD_COLLISION_WORKFLOW_ID", "") or LIVE_WORKFLOW_ID
    )

    # The richer live pilot keeps its original, fully-described route.
    if target_id == LIVE_WORKFLOW_ID:
        return ResolvedWorkflowRoute(
            vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
            workflow_id=LIVE_WORKFLOW_ID,
            workflow_version=LIVE_WORKFLOW_VERSION,
            config_json={
                "route_type": "birchwood_collision_demo_placeholder",
                "display_name": BIRCHWOOD_COLLISION_DISPLAY_NAME,
                "powered_by": BIRCHWOOD_COLLISION_POWERED_BY,
                "client_target": BIRCHWOOD_COLLISION_CLIENT_TARGET,
                "status": BIRCHWOOD_COLLISION_STATUS,
            },
            fallback_used=False,
            audit_metadata={
                "routing_source": "configured_phone_number",
                "configured_key": "BIRCHWOOD_COLLISION_PHONE_NUMBER",
                "called_phone_number_masked": _mask_phone(called_phone_number),
                "environment": getattr(config, "ENVIRONMENT", "development"),
                "vertical": AUTOMOTIVE_COLLISION_VERTICAL,
                "workflow_id": LIVE_WORKFLOW_ID,
                "display_name": BIRCHWOOD_COLLISION_DISPLAY_NAME,
                "powered_by": BIRCHWOOD_COLLISION_POWERED_BY,
                "client_target": BIRCHWOOD_COLLISION_CLIENT_TARGET,
                "status": BIRCHWOOD_COLLISION_STATUS,
            },
        )

    # Any other configured collision workflow (default: the minimal pure-intake
    # one) — build the route from its registered definition. Fall back to the
    # live pilot if the configured id is not registered (fail safe).
    try:
        definition = (
            ensure_default_workflows_registered().get(target_id).get_definition()
        )
    except Exception:
        logger.error(
            "[Routing] BIRCHWOOD_COLLISION_WORKFLOW_ID '%s' is not registered — "
            "falling back to the live pilot route",
            target_id,
        )
        return ResolvedWorkflowRoute(
            vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
            workflow_id=LIVE_WORKFLOW_ID,
            workflow_version=LIVE_WORKFLOW_VERSION,
            config_json={
                "route_type": "birchwood_collision_demo_placeholder",
                "display_name": BIRCHWOOD_COLLISION_DISPLAY_NAME,
                "powered_by": BIRCHWOOD_COLLISION_POWERED_BY,
                "client_target": BIRCHWOOD_COLLISION_CLIENT_TARGET,
                "status": BIRCHWOOD_COLLISION_STATUS,
            },
            fallback_used=False,
            audit_metadata={
                "routing_source": "configured_phone_number",
                "configured_key": "BIRCHWOOD_COLLISION_PHONE_NUMBER",
                "called_phone_number_masked": _mask_phone(called_phone_number),
                "vertical": AUTOMOTIVE_COLLISION_VERTICAL,
                "workflow_id": LIVE_WORKFLOW_ID,
                "fallback_reason": "configured_workflow_not_registered",
            },
        )

    return ResolvedWorkflowRoute(
        vertical_key=definition.vertical,
        workflow_id=definition.workflow_id,
        workflow_version=definition.version,
        config_json={
            "route_type": "birchwood_collision_phone_number",
            "display_name": definition.display_name,
        },
        fallback_used=False,
        audit_metadata={
            "routing_source": "configured_phone_number",
            "configured_key": "BIRCHWOOD_COLLISION_PHONE_NUMBER",
            "called_phone_number_masked": _mask_phone(called_phone_number),
            "environment": getattr(config, "ENVIRONMENT", "development"),
            "vertical": definition.vertical,
            "workflow_id": definition.workflow_id,
            "display_name": definition.display_name,
        },
    )


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[\s().-]+", "", value.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    return cleaned or None


def _mask_phone(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        return mask_phi(value)
    except Exception:
        return f"***{value[-4:]}" if len(value) >= 4 else "***"
