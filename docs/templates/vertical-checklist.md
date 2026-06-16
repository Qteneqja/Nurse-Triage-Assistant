# New Vertical Checklist

Copy this into the PR/issue when adding or extending an ORCA vertical. It is the
checklist form of [.claude/skills/adding-a-vertical/SKILL.md](../../.claude/skills/adding-a-vertical/SKILL.md).
Reference pattern: `src/verticals/insurance/` (class-based) and
`src/verticals/automotive_collision/` (spec-driven).

**Vertical name:** `____`  **Workflow id:** `____v1`  **Default? (should be NO):** ____

## Package & schemas
- [ ] `src/verticals/<name>/` created (`__init__, constants, schemas, rules, prompts, extraction, workflow` [+ `spec`]); core untouched.
- [ ] `constants.py`: vertical key, workflow id/version, dispositions, required fields, output type, disclaimers.
- [ ] `schemas.py` (Pydantic v2): `<Name>Intake`, `<Name>Assessment` (disposition, rules_triggered, safety_flags, human_review_required, missing_information, confidence), `<Name>Record`.

## Deterministic rules & safety
- [ ] `rules.py`: pure `classify_<name>(intake, dynamic_text="")` — no LLM, emergency patterns first, **fails closed to human review**. Mirrors `src/safety/red_flags.py`.
- [ ] Healthcare red-flag / injury safety overlay still applies (router overlay not bypassed).
- [ ] Restricted-advice boundaries defined and enforced deterministically (no binding promises from the LLM). *(safety change → read the safety skill)*

## Workflow & seam
- [ ] `workflow.py` implements `BaseWorkflow` (`get_definition`, `start_session`, `handle_turn`, `finalize`) [+ scripted intake / extraction schema / spec].
- [ ] `prompts.py` voice prompts; `extraction.py` `BaseExtractionAgent` with versioned `schema_version`.
- [ ] Registered in `registry.py` `ensure_default_workflows_registered()` with `make_default=False`.
- [ ] Routing added in `router.py` (phone route backed by `<NAME>_PHONE_NUMBER`, or `WORKFLOW_PHONE_ROUTES`).

## Demo pack (`demo/<name>/`)
- [ ] `scenarios.json` with ≥ 8 scenarios (incl. expected_routing / key_fields / disclaimers / output file).
- [ ] `expected_outputs/<id>.json` per scenario (expected extraction + dispositions).
- [ ] `transcripts/<id>.md` per scenario.
- [ ] Placeholder phone numbers only; no real PII; no forbidden promise language; no hidden/BIDI Unicode.

## Tests (`tests/`)
- [ ] Registration does **not** change the healthcare default.
- [ ] Definition metadata (id, vertical, required fields, output types).
- [ ] Scripted intake stage order / allowed values.
- [ ] Rules: input → expected disposition (incl. emergency + missing-field cases).
- [ ] End-to-end workflow execution (handle_turn → finalize → structured output).
- [ ] Routing: `<NAME>_PHONE_NUMBER` → correct vertical_key / workflow_id.
- [ ] Extraction agent entities/flags.
- [ ] Demo-pack test (scenario count, required fields, no forbidden phrases).

## Gate to merge
- [ ] `ruff check` + `ruff format --check` clean.
- [ ] Full suite green: `python -m pytest tests/ -v`.
- [ ] No safety regression: deterministic golden-call dispositions unchanged.
- [ ] `bandit` + gitleaks clean; no secrets; CLAUDE.md / skill / ADR updated if architecture changed.
