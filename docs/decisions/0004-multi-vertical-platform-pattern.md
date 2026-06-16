# ADR 0004 — Multi-vertical platform pattern: one engine, config-per-vertical

- **Status:** Accepted (v5.1.0)
- **Date:** 2026-06-16
- **Related:** [.claude/skills/adding-a-vertical/SKILL.md](../../.claude/skills/adding-a-vertical/SKILL.md), [docs/WORKFLOW_ENGINE.md](../WORKFLOW_ENGINE.md), [docs/templates/vertical-checklist.md](../templates/vertical-checklist.md)

## Context

The business plan is to grow one safety-critical engine across many verticals: healthcare
triage (safety-critical showcase), Birchwood collision intake (live pilot), insurance
FNOL, and property-management maintenance. Each vertical needs its own intake fields,
deterministic routing rules, dispositions, prompts, and demo data — but they must share
the orchestrator turn loop, the safety gate, observability, and storage. Copy-forking the
core per vertical would multiply the safety surface and let the verticals drift.

## Decision

Adopt a **shared engine + config-per-vertical** pattern. New verticals are added as
packages under `src/verticals/<name>/` and wired in through a config/registration seam —
**never by forking `src/orchestrator/` or `src/safety/`.**

The seam:
- **Workflow contract:** `BaseWorkflow` (`get_definition` / `start_session` /
  `handle_turn` / `finalize` + optional hooks) — [src/platform/workflows/base.py](../../src/platform/workflows/base.py).
- **Declarative option:** `WorkflowSpec` + `SpecDrivenWorkflow` for spec-defined
  workflows (Birchwood is the first) — [docs/WORKFLOW_ENGINE.md](../WORKFLOW_ENGINE.md).
- **Registration:** `registry.register(...)` in `ensure_default_workflows_registered()`
  ([src/platform/workflows/registry.py](../../src/platform/workflows/registry.py)); new
  verticals register with `make_default=False` so healthcare stays the default.
- **Routing:** phone-number / org routes resolve a call to a workflow
  ([src/platform/workflows/router.py](../../src/platform/workflows/router.py)).
- **Per-vertical files:** `constants`, `schemas`, `rules` (deterministic, mirroring
  `src/safety/red_flags.py`), `extraction`, `prompts`, `workflow` (+ `spec`).
- **Safety overlay is hard-wired, not per-vertical:** the router enforces an injury safety
  overlay after every turn/finalize ([router.py:154-227](../../src/platform/workflows/router.py#L154-L227)),
  so a non-clinical vertical cannot opt out of escalation.

## Consequences

- **+** New verticals ship without re-touching the audited core; one safety gate serves
  all; demo packs + per-vertical tests keep behavior pinned.
- **−** The shared contract constrains how unusual a vertical can be; the spec engine and
  router must absorb new routing/disposition shapes. Healthcare deliberately stays
  partly outside the generic spec engine (`get_spec()` returns `None`) for isolation,
  which is an intentional asymmetry to maintain.
- **Guardrail:** verticals add behavior; they never weaken safety. Healthcare red flags
  apply on top of any vertical's own rules.

## Alternatives rejected

- *Fork the core per vertical:* rejected — multiplies the safety surface and invites drift.
- *Single mega-workflow with vertical `if`-branches:* rejected — unbounded complexity in
  the safety-critical path; the registry/spec seam keeps verticals isolated and testable.
