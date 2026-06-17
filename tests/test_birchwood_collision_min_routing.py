"""Cut-over routing for the minimal collision workflow.

The Birchwood phone number now routes to the minimal pure-intake workflow
(birchwood_collision_intake_min_v1) by DEFAULT. It is reversible via the
BIRCHWOOD_COLLISION_WORKFLOW_ID env var (set it to the live pilot id to route
back), falls back safely to the live pilot if the configured id is unregistered,
and a DB phone_numbers route (when present) still takes precedence over this
config route. The real number lives in the deployed env; tests use a placeholder.
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
_MIN_ID = "birchwood_collision_intake_min_v1"
_LIVE_ID = "birchwood_collision_intake_v1"


def _resolve(
    *, workflow_id_override: str | None = None, phone_routes: dict | None = None
):
    reset_workflow_registry()
    ensure_default_workflows_registered()
    patches = [
        patch.object(config, "BIRCHWOOD_COLLISION_PHONE_NUMBER", _PLACEHOLDER_NUMBER),
        patch.object(config, "WORKFLOW_PHONE_ROUTES", phone_routes or {}),
    ]
    if workflow_id_override is not None:
        patches.append(
            patch.object(
                config, "BIRCHWOOD_COLLISION_WORKFLOW_ID", workflow_id_override
            )
        )
    with patches[0], patches[1]:
        if workflow_id_override is not None:
            with patches[2]:
                return WorkflowRouteResolver(repository=None).resolve(
                    _PLACEHOLDER_NUMBER
                )
        return WorkflowRouteResolver(repository=None).resolve(_PLACEHOLDER_NUMBER)


def test_birchwood_number_defaults_to_minimal_workflow():
    route = _resolve()
    assert route.workflow_id == _MIN_ID
    assert route.vertical_key == "automotive_collision_min"


def test_revert_to_live_pilot_via_env():
    route = _resolve(workflow_id_override=_LIVE_ID)
    assert route.workflow_id == _LIVE_ID
    assert route.vertical_key == "automotive_collision"


def test_unknown_configured_workflow_falls_back_to_live():
    route = _resolve(workflow_id_override="does_not_exist_v1")
    assert route.workflow_id == _LIVE_ID


def test_workflow_phone_routes_still_overrides():
    # The generic WORKFLOW_PHONE_ROUTES map is checked before the built-in
    # Birchwood route, so it can point the number anywhere too.
    route = _resolve(phone_routes={_PLACEHOLDER_NUMBER: _MIN_ID})
    assert route.workflow_id == _MIN_ID
