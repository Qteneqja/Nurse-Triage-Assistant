# ADR 0001 — Fail-closed safety hierarchy and over-escalation default

- **Status:** Accepted (reflects shipped behavior, v5.1.0)
- **Date:** 2026-06-16
- **Related:** [CLAUDE.md §3](../../CLAUDE.md), [.claude/skills/modifying-safety-orchestrator/SKILL.md](../../.claude/skills/modifying-safety-orchestrator/SKILL.md)

## Context

ORCA conducts unsupervised phone intake and emits a disposition that can route a caller
to (or away from) emergency care. An LLM is part of the pipeline, but LLMs hallucinate,
can be prompt-injected, and can wrongly reassure. A wrong "you're fine" is potentially
fatal; a wrong "go to the ER" is merely inconvenient. The system must therefore be
**asymmetrically conservative** and must never let a probabilistic component have the
final say on safety.

## Decision

Enforce a strict, fail-closed authority hierarchy with over-escalation as the default:

**RED FLAGS > DETERMINISTIC RULES > PROTOCOL > LLM**

1. **Deterministic pre-check before any LLM call.** `score_red_flags()` runs first each
   turn: any critical flag → `ER_NOW`, weighted score ≥ 10 → `URGENT`, both short-circuit
   with no LLM call ([src/safety/red_flags.py:285-351](../../src/safety/red_flags.py#L285-L351),
   [src/orchestrator/orchestrator.py:475-635](../../src/orchestrator/orchestrator.py#L475-L635)).
2. **The LLM is never the last word.** All LLM output passes through one gate —
   `gate_triage_output()` / `gate_outbound_text()` — with no bypass
   ([src/safety/gate.py:1-11](../../src/safety/gate.py#L1-L11)). The gate layers red-flag
   override → diagnosis enforcement → schema validation → protocol hierarchy →
   confidence floor → canonical normalization ([src/safety/gate.py:361-512](../../src/safety/gate.py#L361-L512)).
3. **No downgrade.** The LLM cannot lower a prior disposition (post-check) and weighted
   flags (0 < score < 10) upgrade `SELF_CARE`/`SCHEDULE` to `URGENT`
   ([src/orchestrator/validators.py:380-388](../../src/orchestrator/validators.py#L380-L388),
   [src/safety/gate.py:464-472](../../src/safety/gate.py#L464-L472)).
4. **Over-escalation default / fail closed.** Any exception, timeout, JSON/schema
   failure, post-check violation, or unknown disposition resolves to
   `HUMAN_REVIEW`/escalation with a safe fallback message — never reassurance. Even a
   red-flag *rule exception* is treated as triggered
   ([src/safety/gate.py:515-537](../../src/safety/gate.py#L515-L537),
   [src/safety/red_flag_rules.py:372-381](../../src/safety/red_flag_rules.py#L372-L381)).
5. **Auditability.** Every turn writes a `DecisionTraceEntry` + `AuditTrace` step so any
   decision can be reconstructed ([src/orchestrator/schemas.py:540-797](../../src/orchestrator/schemas.py#L540-L797)).

The invariants are guarded by architectural tests (`tests/test_no_bypass.py`,
`tests/test_canonical_enforcement.py`) and behavioral tests (`tests/test_phase1_safety.py`,
`tests/test_red_flags.py`, `tests/golden_calls/`).

## Consequences

- **+** Life-threatening false negatives are structurally minimized; decisions are
  reproducible and auditable; the LLM can be improved or swapped without weakening safety.
- **−** Higher false-positive escalation rate (accepted by design) and more human-review
  load. Deterministic red-flag regex requires curation and can over-trigger.
- **Operational rule:** safety logic is **additive-only**. Any change ships with new
  escalation tests and must not regress golden-call escalation coverage.

## Alternatives rejected

- *LLM-decides-with-guardrail-prompts:* rejected — prompts are not a control; injection
  and hallucination defeat them.
- *Symmetric confidence (treat under/over-escalation equally):* rejected — the clinical
  cost is asymmetric.
