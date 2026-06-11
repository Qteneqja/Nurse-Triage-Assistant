# Workflow Engine — Spec-Defined Workflows (PR 3)

ORCA workflows are defined declaratively by a **WorkflowSpec**
([src/platform/workflows/spec.py](../src/platform/workflows/spec.py)) and run
by **SpecDrivenWorkflow**
([src/platform/workflows/spec_workflow.py](../src/platform/workflows/spec_workflow.py))
under the **WorkflowEngine**
([src/platform/workflows/router.py](../src/platform/workflows/router.py)).
Birchwood (`birchwood_collision_intake_v1`) is the first complete workflow on
this engine; its spec lives in
[src/verticals/automotive_collision/spec.py](../src/verticals/automotive_collision/spec.py).

## What a spec contains

| Field | Meaning |
|---|---|
| `workflow_id`, `vertical`, `version`, `display_name` | Identity (reserved ids/verticals rejected — see Safety) |
| `greeting` | Spoken intro at call start |
| `stages[]` | Scripted stages: prompt, field, type, voice timeouts, `multi_segment` (narrative capture), `dynamic_prompt` (readback) |
| `required_fields` / `optional_fields` | Required = asked via gap-fill when the story didn't answer them; optional = captured opportunistically, never interrogated |
| `extraction_entities` | Post-call extraction schema entities |
| `completion_rules` | NAME of a registered rules callable (default `default_required_fields_v1`: complete iff all required fields present) |
| `fallback_route` | Callback/transfer behavior + message when the intake can't complete |
| `summary_templates` | `caller` and `business` templates; `{field}` placeholders substitute safely (missing → `unknown`, never crashes a call) |
| `dashboard_display_fields` | The record fields the dashboard lists (PR 4 contract) |
| `recommended_actions` | outcome → next action for staff |
| `final_messages` | outcome → spoken closing override |
| `safety_hooks` | DOCUMENTATION of which hard-wired branches apply — not a switch (see Safety) |
| `narrative_extractor`, `field_recorded_hook`, `dynamic_prompt_builder`, `record_builder` | Optional NAMES of registered hook callables |

Hooks are resolved from a code-side registry
(`register_hook(kind, name, fn)`) — a definition FILE can reference behavior
by name but can never inject code. Unknown hook names fail the definition at
load time.

## Adding a workflow: one file + config

1. Drop a JSON file in `EXTRA_WORKFLOW_DEFINITIONS_DIR` (or
   `src/platform/workflows/definitions/` for built-ins):

```json
{
  "workflow_id": "towing_followup_v1",
  "vertical": "roadside",
  "display_name": "Towing Follow-up",
  "greeting": "Thanks for calling the towing follow-up line.",
  "stages": [
    {"stage_id": "REASON", "field_name": "reason",
     "prompt": "What do you need help with today?"},
    {"stage_id": "CALLER_NAME", "field_name": "caller_name",
     "prompt": "What's your full name?"},
    {"stage_id": "PHONE", "field_name": "phone",
     "prompt": "Best callback number?", "field_type": "phone"}
  ],
  "required_fields": ["reason", "caller_name", "phone"],
  "dashboard_display_fields": ["caller_name", "phone", "reason"],
  "recommended_actions": {
    "COMPLETED_INTAKE": "Call the customer back about their tow."
  },
  "summary_templates": {
    "caller": "Thanks {caller_name}, the team will call you back.",
    "business": "{outcome} for {caller_name}."
  }
}
```

2. Route a number to it in config:

```bash
WORKFLOW_PHONE_ROUTES={"+15555550150": "towing_followup_v1"}
EXTRA_WORKFLOW_DEFINITIONS_DIR=/path/to/definitions
```

That's it — no core code changes. `tests/test_pr3_workflow_engine.py::`
`test_toy_workflow_from_definition_file_runs_end_to_end` demonstrates exactly
this through the real voice channel. Invalid definitions (bad JSON, schema
violations, reserved ids, unknown hooks) are rejected at startup with an
ERROR log and never affect built-in workflows.

## Routing

Resolution order for an inbound call:

1. `phone_numbers` rows in Postgres (production seeding)
2. **`WORKFLOW_PHONE_ROUTES`** (JSON env map, number → workflow_id)
3. Legacy per-vertical envs (`INSURANCE_FNOL_PHONE_NUMBER`,
   `BIRCHWOOD_COLLISION_PHONE_NUMBER`)
4. Shared-number vertical menu (`ENABLE_SHARED_NUMBER_VERTICAL_MENU`)
   for healthcare/insurance/collision demos
5. Default workflow fallback (`ENABLE_DEFAULT_WORKFLOW_ROUTE`)

## Safety — hard-wired beneath the workflow layer

A workflow definition **cannot** disable or weaken safety behavior:

- **Injury branch (non-clinical verticals):** the WorkflowEngine scans every
  turn/finalize result's caller text and FORCES `injuries_reported`
  (first-in-flags), `human_review_required=true`, the
  `platform:injury_safety_branch` rule, and the spoken 9-1-1 advisory
  (at most once per call) — regardless of what the spec declares.
  `tests/test_pr3_workflow_engine.py::test_toy_workflow_injury_mention_cannot_bypass_safety`
  proves a spec with zero safety configuration still gets all of this.
- **Healthcare is unreachable from specs:** `workflow_id=healthcare_triage_v1`
  and `vertical=healthcare` are reserved — spec validation rejects them, and
  the registry refuses to replace a registered reserved workflow. The engine
  overlay passes healthcare results through untouched; its safety stack
  (RED FLAGS > DETERMINISTIC RULES > PROTOCOL > LLM) lives in the
  orchestrator and is not part of the workflow definition surface at all.
- Healthcare stays on its proven code path for the pilot (`get_spec()`
  returns `None`); registry/routing treat it uniformly via
  `get_definition()`. Engine migration is explicitly post-pilot with its own
  safety re-validation.

## Birchwood on the engine

`BirchwoodCollisionIntakeWorkflow` subclasses `SpecDrivenWorkflow` with
`build_birchwood_spec()` — the complete declarative definition (16 stages,
12 required fields, extraction entities, per-outcome recommended actions and
closings, dashboard fields, `injury_safety_branch`). Its vertical-rich
behaviors (deterministic routing gates, narrative extraction, readback
builder, record shape) are registered as named hooks
(`automotive_collision_birchwood_rules_v1`, `birchwood_narrative_extractor_v1`,
`birchwood_readback_v1`, `birchwood_record_builder_v1`) and its overrides keep
record/message output byte-identical to the pre-engine implementation — the
full Birchwood test suite is the equivalence proof. Stage voice timeouts read
live config, so the stage list is built per call
(`_scripted_stage_definitions()`), mirrored into the spec.

Insurance FNOL remains on its own BaseWorkflow implementation (migration was
not free); it is registry-uniform like healthcare.
