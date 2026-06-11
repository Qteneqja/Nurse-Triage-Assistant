"""Deterministic workflow registry."""

from __future__ import annotations

from threading import RLock

from src.platform.workflows.base import BaseWorkflow
from src.platform.workflows.schemas import WorkflowDefinition


class WorkflowNotFoundError(KeyError):
    """Raised when a workflow cannot be resolved from the registry."""


class WorkflowRegistry:
    """Small, testable registry keyed by workflow_id."""

    def __init__(self) -> None:
        self._workflows: dict[str, BaseWorkflow] = {}
        self._defaults_by_vertical: dict[str, str] = {}

    def register(self, workflow: BaseWorkflow, *, make_default: bool = True) -> None:
        definition = workflow.get_definition()
        # Reserved workflows (the healthcare safety stack) can never be
        # replaced once registered — a spec or plugin that tries is refused.
        from src.platform.workflows.spec import RESERVED_WORKFLOW_IDS

        existing = self._workflows.get(definition.workflow_id)
        if (
            definition.workflow_id in RESERVED_WORKFLOW_IDS
            and existing is not None
            and type(existing) is not type(workflow)
        ):
            raise ValueError(
                f"Workflow id '{definition.workflow_id}' is reserved and "
                "already registered — it cannot be replaced."
            )
        self._workflows[definition.workflow_id] = workflow
        if make_default or definition.vertical not in self._defaults_by_vertical:
            self._defaults_by_vertical[definition.vertical] = definition.workflow_id

    def get(self, workflow_id: str) -> BaseWorkflow:
        try:
            return self._workflows[workflow_id]
        except KeyError as exc:
            raise WorkflowNotFoundError(
                f"Workflow '{workflow_id}' is not registered"
            ) from exc

    def list_workflows(self) -> list[WorkflowDefinition]:
        return [
            self._workflows[key].get_definition()
            for key in sorted(self._workflows.keys())
        ]

    def get_default_for_vertical(self, vertical: str) -> BaseWorkflow:
        workflow_id = self._defaults_by_vertical.get(vertical)
        if workflow_id is None:
            raise WorkflowNotFoundError(
                f"No default workflow is registered for vertical '{vertical}'"
            )
        return self.get(workflow_id)


_registry: WorkflowRegistry | None = None
_registry_lock = RLock()
_defaults_registered = False


def get_workflow_registry() -> WorkflowRegistry:
    """Return the process-local workflow registry singleton."""

    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = WorkflowRegistry()
        return _registry


def reset_workflow_registry() -> None:
    """Reset the singleton registry. Intended for tests."""

    global _registry, _defaults_registered
    with _registry_lock:
        _registry = None
        _defaults_registered = False


def ensure_default_workflows_registered() -> WorkflowRegistry:
    """Register built-in workflows exactly once."""

    global _defaults_registered
    registry = get_workflow_registry()
    with _registry_lock:
        if not _defaults_registered:
            from src.verticals.healthcare.workflow import HealthcareTriageWorkflow
            from src.verticals.property_management.workflow import (
                PropertyManagementMaintenanceWorkflow,
            )
            from src.verticals.insurance.workflow import InsuranceClaimsFnolWorkflow
            from src.verticals.automotive_collision.workflow import (
                BirchwoodCollisionIntakeWorkflow,
            )

            registry.register(HealthcareTriageWorkflow(), make_default=True)
            registry.register(
                PropertyManagementMaintenanceWorkflow(),
                make_default=False,
            )
            registry.register(
                InsuranceClaimsFnolWorkflow(),
                make_default=False,
            )
            registry.register(
                BirchwoodCollisionIntakeWorkflow(),
                make_default=False,
            )
            # Spec-defined workflows: built-in definitions/ directory plus
            # the operator-configured EXTRA_WORKFLOW_DEFINITIONS_DIR. A bad
            # definition file is skipped with an error — it can never block
            # the built-in workflows from starting.
            from src.platform.workflows.spec_loader import (
                register_spec_definitions,
            )

            register_spec_definitions(registry)
            _defaults_registered = True
    return registry
