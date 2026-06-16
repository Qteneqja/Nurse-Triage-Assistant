---
name: adding-a-vertical
description: Load when shipping a new ORCA vertical (e.g. a new intake domain) without rebuilding the core. The repeatable recipe — intake schema, deterministic escalation rules mirroring red_flags, restricted-advice boundaries, workflow + routing registration via the config seam, demo pack, and required tests. References the healthcare and insurance-FNOL workflows as the pattern.
---

# Adding a Vertical

ORCA grows by **config-per-vertical over one shared engine**. You do NOT touch
`src/orchestrator/` or `src/safety/`. You add a package under `src/verticals/<name>/`
and register it through the workflow seam. See ADR 0004.

## When to load this skill

Adding a new intake domain/vertical, or extending an existing one's schema, rules,
routing, or demo pack.

## Reference implementations

- **Spec-driven (preferred for new verticals):** `src/verticals/automotive_collision/`
  (Birchwood, `birchwood_collision_intake_v1`) built on `WorkflowSpec` +
  `SpecDrivenWorkflow`. See [docs/WORKFLOW_ENGINE.md](../../../docs/WORKFLOW_ENGINE.md).
- **Class-based BaseWorkflow:** `src/verticals/insurance/` (`insurance_claims_fnol_v1`).
- **Healthcare** (`src/verticals/healthcare/`) is the safety-critical exemplar;
  `get_spec()` returns `None` to keep it isolated from the generic spec engine.

## The workflow contract (`src/platform/workflows/base.py`)

A vertical workflow subclasses `BaseWorkflow` and implements:

- `get_definition() -> WorkflowDefinition` — id, vertical, version, display_name,
  required_fields, supported/default output types
  ([base.py:21-24](../../../src/platform/workflows/base.py#L21-L24)).
- `start_session(context) -> dict` — initial session state.
- `async handle_turn(context, input) -> WorkflowTurnResult` — must return
  `should_continue`, `should_finalize`, `escalation_required`,
  `recommended_disposition`, `rules_triggered`, `safety_events`.
- `async finalize(context, session_state) -> WorkflowFinalResult`.

Optional hooks: `get_extraction_schema()`, `get_scripted_intake_definition()`,
`get_spec()`, `prefill_from_narrative()`, `on_field_recorded()`, `build_dynamic_prompt()`.

## Recipe

### 1. Define the package layout
`src/verticals/<name>/`: `__init__.py`, `constants.py`, `schemas.py`, `rules.py`,
`prompts.py`, `extraction.py`, `workflow.py` (+ `spec.py` if spec-driven).

### 2. Intake schema (`schemas.py`, Pydantic v2)
Three models, following insurance as the pattern
([src/verticals/insurance/schemas.py](../../../src/verticals/insurance/schemas.py)):
- **`<Name>Intake`** — fields captured during the call.
- **`<Name>Assessment`** — the deterministic decision: `disposition`,
  `rules_triggered: list[str]`, `safety_flags: list[str]`, `human_review_required: bool`,
  `missing_information: list[str]`, `confidence`.
- **`<Name>Record`** — combined intake + assessment for storage/dashboard.

### 3. Deterministic escalation module (`rules.py`) — mirrors `src/safety/red_flags.py`
Export a pure classifier `classify_<name>(intake, dynamic_text="") -> <Name>Assessment`.
Use the same conservative, regex/condition pattern as the red-flag engine
([src/verticals/insurance/rules.py:18-150](../../../src/verticals/insurance/rules.py#L18-L150)):
deterministic, no LLM, emergency patterns first, then missing-field → docs-needed /
human-review, else standard. Emit machine-readable rule ids
(e.g. `"<name>:emergency:active_fire"`). **Fail closed to a human-review disposition.**

> Healthcare red flags ALWAYS apply on top via the platform safety overlay (below);
> a non-clinical vertical's rules are additive, never a replacement for them.

### 4. Restricted-advice boundaries (post-check)
There is no per-vertical advice-boundary module yet; the platform enforces a **hard-wired
injury safety overlay** in the router that runs after every turn and at finalization
([src/platform/workflows/router.py:154-227](../../../src/platform/workflows/router.py#L154-L227)):
it scans caller/assistant text for injuries, flags them, and forces human review. To add
vertical advice boundaries:
- List forbidden phrases in `constants.py` (e.g. no coverage/approval promises — see the
  Birchwood demo-pack guard for "approved"/"covered" forbidden language).
- Enforce them deterministically post-turn (extend the router overlay pattern or a
  vertical hook); never let the LLM emit a binding promise.
This is a safety change — also read
[.claude/skills/modifying-safety-orchestrator/SKILL.md](../modifying-safety-orchestrator/SKILL.md).

### 5. Routing dispositions + workflow registration (the config seam)
- Define the vertical's disposition taxonomy in `constants.py` (insurance:
  `EMERGENCY_SERVICES_NOW, URGENT_ADJUSTER_REVIEW, STANDARD_CLAIM_INTAKE, DOCUMENTS_NEEDED,
  INFORMATION_ONLY, HUMAN_REVIEW`).
- **Register** in `ensure_default_workflows_registered()`
  ([src/platform/workflows/registry.py:89-128](../../../src/platform/workflows/registry.py#L89-L128)):
  `registry.register(<Name>Workflow(), make_default=False)` (do NOT change the healthcare
  default).
- **Route** by adding a phone-number route in
  [src/platform/workflows/router.py](../../../src/platform/workflows/router.py) (mirror
  the insurance/Birchwood routes) backed by a `<NAME>_PHONE_NUMBER` config var, or via the
  generic `WORKFLOW_PHONE_ROUTES` map.

### 6. Prompts & extraction
- `prompts.py` — `PROMPTS = {field_name: "voice prompt text", ...}`.
- `extraction.py` — a `BaseExtractionAgent` subclass mapping the final result to
  analytics entities with a versioned `schema_version`.

### 7. Demo pack (`demo/<name>/`)
Mirror `demo/insurance_fnol/` and `demo/birchwood_collision/`:
- `scenarios.json` — ≥ 8 scenarios (id, title, caller profile, scripted turns/answers,
  `expected_routing`, `expected_key_fields`, `expected_disclaimers`, `expected_output_file`).
- `expected_outputs/<id>.json` — full record dump (the expected extraction + dispositions).
- `transcripts/<id>.md` — readable demo transcripts.
- Use placeholder phone numbers only (e.g. `+15555550140`); no real PII; no forbidden
  promise language; no hidden/BIDI Unicode (enforced by the demo-pack tests).

### 8. Required tests (`tests/`)
Model on `tests/test_insurance_claims_workflow.py`,
`tests/test_automotive_collision_workflow.py`, `tests/test_phase11_routing.py`,
`tests/test_insurance_demo_pack.py`:
- Registration: workflow registers **without changing the healthcare default**.
- Definition metadata (id, vertical, required_fields, output types).
- Scripted intake stage order / allowed values.
- Deterministic rules: input → expected disposition cases (incl. emergency + missing-field).
- Workflow execution end-to-end (handle_turn → finalize → structured output).
- Routing: `<NAME>_PHONE_NUMBER` → correct `vertical_key`/`workflow_id`.
- Extraction agent produces the expected entities/flags.
- Demo pack: scenario count, required fields present, no forbidden phrases.

## Checklist

- [ ] New package under `src/verticals/<name>/`; core untouched.
- [ ] Intake / Assessment / Record schemas (Pydantic v2).
- [ ] Deterministic `classify_<name>()` rules, fail-closed to human review.
- [ ] Healthcare red-flag overlay still applies; advice boundaries enforced deterministically.
- [ ] Registered via `registry.register(..., make_default=False)`; routing added.
- [ ] Demo pack with ≥ 8 scenarios + expected outputs + transcripts (placeholder data).
- [ ] All required tests added and the full suite stays green.

## Sources

Derived from reading: `src/platform/workflows/{base,registry,router,schemas,spec,spec_workflow}.py`,
`src/verticals/{healthcare,insurance,automotive_collision,property_management}/*`,
`docs/WORKFLOW_ENGINE.md`, and `tests/{test_insurance_claims_workflow,
test_automotive_collision_workflow,test_phase11_routing,test_insurance_demo_pack,
test_birchwood_collision_demo_pack}.py`.
