"""Cut-over routing for the minimal collision workflow.

The Birchwood phone line uses the live pilot (birchwood_collision_intake_v1) by
default. Setting WORKFLOW_PHONE_ROUTES to map the same number to
birchwood_collision_intake_min_v1 re-points the line to the minimal pure-intake
workflow - it is checked BEFORE the built-in Birchwood route, needs no core
routing code change, and is reversible by unsetting it. The real number lives in
the deployed env; tests use a placeholder.
"""

from __future__ import annotations

from unittest.mock import patch

import src.config as config
from src.platform.workflows.registry import (
    ensure_default_workflows_registered,
    reset_workflow_registry,
)
from src.platform.workflows.router import WorkflowRouteResolver

_PLACEHOLDER_NUMBER = "+15555550140"


def _resolve(routes: dict) -> object:
    reset_workflow_registry()
    ensure_default_workflows_registered()
    with (
        patch.object(config, "BIRCHWOOD_COLLISION_PHONE_NUMBER", _PLACEHOLDER_NUMBER),
        patch.object(config, "WORKFLOW_PHONE_ROUTES", routes),
    ):
        return WorkflowRouteResolver(repository=None).resolve(_PLACEHOLDER_NUMBER)


def test_birchwood_number_defaults_to_live_pilot():
    route = _resolve({})
    assert route.workflow_id == "birchwood_collision_intake_v1"


def test_workflow_phone_routes_cuts_over_to_minimal():
    route = _resolve({_PLACEHOLDER_NUMBER: "birchwood_collision_intake_min_v1"})
    assert route.workflow_id == "birchwood_collision_intake_min_v1"
    assert route.vertical_key == "automotive_collision_min"
