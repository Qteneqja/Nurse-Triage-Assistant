---
name: modifying-safety-orchestrator
description: Load when changing src/safety or src/orchestrator — gates, confidence scoring, decision trace, red-flag/escalation rules. Encodes the inviolable safety invariants, the gates a change must pass, the tests that must stay green, how to add a red flag safely, and the escalation-coverage checklist.
---

# Modifying the Safety Gate & Orchestrator

This is the safety-critical heart of ORCA. Triage decisions can affect a caller's life.
**Changes here are additive-only and over-escalation is always preferred.** If anything
is ambiguous, escalate to a human reviewer rather than guessing.

## When to load this skill

Any change to: `src/safety/**`, `src/orchestrator/**`, the safety gate, red-flag
engines, confidence scoring, the decision trace, or the canonical disposition enum.

## The invariants you must preserve

The fail-closed hierarchy is **RED FLAGS > DETERMINISTIC RULES > PROTOCOL > LLM**
([src/safety/gate.py:151-158](../../../src/safety/gate.py#L151-L158)). A higher tier may
override a lower one; never the reverse.

1. **One gate, no bypass.** All LLM output reaching a caller/file/DB goes through
   `gate_triage_output()` (dispositions) or `gate_outbound_text()` (any text)
   ([src/safety/gate.py:1-11](../../../src/safety/gate.py#L1-L11)). Do not add a second
   decision path or call the LLM client directly — go through `GuardedLLM`.
2. **Pre-check before any LLM call.** `score_red_flags()` runs first each turn: any
   `critical=True` flag → `ER_NOW`; weighted score ≥ 10 → `URGENT`; both short-circuit
   without an LLM call ([src/safety/red_flags.py:285-351](../../../src/safety/red_flags.py#L285-L351),
   [src/orchestrator/orchestrator.py:475-635](../../../src/orchestrator/orchestrator.py#L475-L635)).
3. **Post-check after the LLM.** Diagnoses, unsafe instructions, role/credential claims,
   and PHI are rewritten/removed; the LLM **cannot downgrade** the prior disposition
   ([src/orchestrator/validators.py:354-391](../../../src/orchestrator/validators.py#L354-L391),
   [src/safety/gate.py:265-341](../../../src/safety/gate.py#L265-L341)).
4. **Confidence floor.** Below threshold (0.60), escalation is forced and a
   non-emergency disposition becomes `HUMAN_REVIEW`
   ([src/safety/gate.py:475-489](../../../src/safety/gate.py#L475-L489)).
5. **Weighted-flag no-downgrade.** When 0 < weighted score < 10, `SELF_CARE`/`SCHEDULE`
   is upgraded to `URGENT` ([src/safety/gate.py:464-472](../../../src/safety/gate.py#L464-L472)).
6. **Confused-caller protocol.** Deterministic, non-repetitive retry ladder; the third
   unclear answer escalates to a human
   ([src/orchestrator/orchestrator.py:730-801](../../../src/orchestrator/orchestrator.py#L730-L801)).
7. **Decision-trace contract.** Every turn appends a `DecisionTraceEntry` and the
   `AuditTrace` records each step
   ([src/orchestrator/schemas.py:772-797](../../../src/orchestrator/schemas.py#L772-L797)).
   Never drop, weaken, or skip a trace entry — it is the clinical audit record.
8. **Fail closed → over-escalate.** Any exception, LLM timeout, JSON/schema failure,
   post-check violation, or unknown disposition resolves to `HUMAN_REVIEW`/escalation
   with `SAFE_FALLBACK_MESSAGE` — never reassurance
   ([src/safety/gate.py:515-537](../../../src/safety/gate.py#L515-L537)). Even a red-flag
   *rule evaluation exception* is treated as triggered
   ([src/safety/red_flag_rules.py:372-381](../../../src/safety/red_flag_rules.py#L372-L381)).
9. **Canonical dispositions only.** `ER_NOW | URGENT | SCHEDULE | SELF_CARE | HUMAN_REVIEW`;
   unknown → `HUMAN_REVIEW` ([src/safety/gate.py:36-75](../../../src/safety/gate.py#L36-L75)).

## The gate layers a triage change must pass

`gate_triage_output()` applies these in strict order; understand where your change sits
([src/safety/gate.py:361-512](../../../src/safety/gate.py#L361-L512)):

1. Red-flag rule engine — forced disposition if triggered (LLM output ignored).
2. Diagnosis enforcement — rewrite diagnostic claims.
3. Schema validation — invalid JSON → fail closed to `HUMAN_REVIEW`.
4. Protocol hierarchy — RULES > PROTOCOL > LLM; weighted-flag upgrade.
5. Confidence floor — escalate below threshold.
6. Disposition normalization — canonical enum only.

`gate_outbound_text(text, ctx, kind)` gates ALL caller/file/DB text: role-claim blocker,
diagnosis enforcement, unsafe-instruction removal, PHI masking (when `store_phi=False`),
PHI-probing block, length truncation ([src/safety/gate.py:265-341](../../../src/safety/gate.py#L265-L341)).

## How to add a red-flag / escalation rule safely

Red flags live in two parallel engines — keep them consistent:

- **`src/safety/red_flag_rules.py`** — `RED_FLAG_RULES` registry consumed by the gate
  (`run_red_flag_rules`). Add a `RedFlagRule` with a unique `id`, a deterministic
  `condition(ctx)`, `forced_disposition`, voice-friendly `escalation_script`, `weight`,
  and `critical` ([src/safety/red_flag_rules.py:104-311](../../../src/safety/red_flag_rules.py#L104-L311)).
- **`src/safety/red_flags.py`** — `RedFlagDefinition` lists + `score_red_flags()` used by
  the orchestrator pre-check, and `_UTTERANCE_RULES` (voice scripts) used by
  `check_utterance` ([src/safety/red_flags.py:56-270](../../../src/safety/red_flags.py#L56-L270)).

Rules:
- **Critical = instant ER_NOW** regardless of score. Reserve `critical=True` for
  life-threatening patterns only; everything else is weighted (≥ 10 → URGENT).
- **False positives are acceptable; false negatives for life-threatening patterns are
  not** ([src/safety/red_flags.py:7-11](../../../src/safety/red_flags.py#L7-L11)).
- Patterns are case-insensitive regex over the combined `utterance + chief_complaint +
  red_flags_reported`. Test both the trigger AND innocuous near-misses.
- Add a matching **escalation test** (see below). Never tune a pattern to be *less*
  sensitive without explicit human sign-off.

## Tests that MUST stay green

```bash
python -m pytest tests/test_red_flags.py tests/test_phase1_safety.py \
  tests/test_no_bypass.py tests/test_canonical_enforcement.py \
  tests/test_phase5_safety_patch.py tests/test_validators.py \
  tests/test_orchestrator.py tests/test_protocols.py -v

# Plus the deterministic golden-call regression:
GOLDEN_CALL_MODE=deterministic_only DISABLE_EXTERNAL_CALLS=1 \
  python -m pytest tests/golden_calls/test_golden_calls.py -v
```

- `test_no_bypass.py` / `test_canonical_enforcement.py` — architectural invariants
  (single gate, no direct LLM client, canonical enums, GuardedLLM only). If these fail,
  you broke an invariant — fix the design, not the test.
- `test_phase1_safety.py` — pre-check, scoring, confused-caller, fail-closed paths.
- `test_red_flags.py` — each rule triggers on expected patterns and stays quiet on
  innocuous input.

## Change checklist (must all be "yes")

- [ ] Did I leave the fail-closed hierarchy and single-gate path intact (no bypass)?
- [ ] Does every new failure mode resolve to `HUMAN_REVIEW`/escalation, never reassurance?
- [ ] Are new red flags added to BOTH engines with consistent ids/dispositions?
- [ ] Did I add escalation tests for every new trigger AND a near-miss negative test?
- [ ] Is the decision-trace / audit-trace still populated on every path I touched?
- [ ] Do dispositions stay within the canonical enum (unknown → HUMAN_REVIEW)?
- [ ] Full suite + the safety + golden-call tests pass unchanged?
- [ ] **Did escalation coverage regress?** Compare golden-call dispositions before/after —
      no case may move to a *less* urgent disposition without explicit human sign-off.

## Sources

Derived from reading: `src/safety/gate.py`, `src/safety/red_flags.py`,
`src/safety/red_flag_rules.py`, `src/orchestrator/orchestrator.py`,
`src/orchestrator/validators.py`, `src/orchestrator/schemas.py`,
`src/protocols/retriever.py`, `pytest.ini`, and `tests/` (red_flags, phase1_safety,
no_bypass, canonical_enforcement, phase5_safety_patch, golden_calls).
