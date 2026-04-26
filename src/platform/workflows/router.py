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
    ) -> ResolvedWorkflowRoute | None:
        ...


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
    """Thin dispatcher from route/context to a registered workflow."""

    async def handle_turn(
        self,
        context: WorkflowContext,
        workflow_input: WorkflowInput,
    ) -> WorkflowTurnResult:
        registry = ensure_default_workflows_registered()
        workflow = registry.get(context.workflow_id)
        return await workflow.handle_turn(context, workflow_input)

    async def finalize(
        self,
        context: WorkflowContext,
        session_state: dict,
    ) -> WorkflowFinalResult:
        registry = ensure_default_workflows_registered()
        workflow = registry.get(context.workflow_id)
        return await workflow.finalize(context, session_state)


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
