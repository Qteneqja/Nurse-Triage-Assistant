"""Load spec-defined workflows from JSON definition files (PR 3).

Sources, in order:
1. The built-in ``definitions/`` directory next to this module.
2. ``EXTRA_WORKFLOW_DEFINITIONS_DIR`` (env/config) — the operator drop-in
   directory. Adding a workflow = one JSON file here + a phone route in
   ``WORKFLOW_PHONE_ROUTES``. No core code changes.

Fail-closed per file: an invalid definition (bad JSON, schema violation,
reserved id/vertical, unknown hook name) is rejected and logged; it can
never partially register or affect the built-in workflows. The function
returns a load report so tests and operators can see exactly what was
accepted and why anything was refused.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import src.config as config
from src.platform.workflows.spec import WorkflowSpec
from src.platform.workflows.spec_workflow import SpecDrivenWorkflow

logger = logging.getLogger(__name__)

BUILTIN_DEFINITIONS_DIR = Path(__file__).parent / "definitions"


@dataclass
class SpecLoadReport:
    loaded: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (file, reason)


def _definition_dirs() -> list[Path]:
    dirs = [BUILTIN_DEFINITIONS_DIR]
    extra = (getattr(config, "EXTRA_WORKFLOW_DEFINITIONS_DIR", "") or "").strip()
    if extra:
        dirs.append(Path(extra))
    return dirs


def register_spec_definitions(registry) -> SpecLoadReport:
    report = SpecLoadReport()
    for directory in _definition_dirs():
        if not directory.is_dir():
            continue
        for json_file in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(json_file.read_text(encoding="utf-8"))
                spec = WorkflowSpec.model_validate(payload)
                workflow = SpecDrivenWorkflow(spec)
                registry.register(workflow, make_default=False)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                report.rejected.append((json_file.name, reason))
                logger.error(
                    "[WorkflowSpec] REJECTED definition %s — %s",
                    json_file.name,
                    reason,
                )
                continue
            report.loaded.append(spec.workflow_id)
            logger.info(
                "[WorkflowSpec] Registered spec-defined workflow '%s' from %s",
                spec.workflow_id,
                json_file.name,
            )
    return report
