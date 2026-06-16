# ADR 0003 — LLM provider abstraction + DeepSeek/PHI governance and BAA seam

- **Status:** Accepted (current state) with a proposed abstraction seam
- **Date:** 2026-06-16
- **Related:** [.claude/skills/modifying-safety-orchestrator/SKILL.md](../../.claude/skills/modifying-safety-orchestrator/SKILL.md), ADR 0001

## Context

ORCA uses an LLM for question generation, Phase-1 reasoning, and SBAR finalization. Today
the only provider is **DeepSeek**, reached through an `AsyncOpenAI`-compatible client
([src/llm/deepseek_client.py](../../src/llm/deepseek_client.py),
[src/llm/client.py](../../src/llm/client.py)) and wrapped by **`GuardedLLM`**
([src/llm/guarded_client.py](../../src/llm/guarded_client.py)). Config:
`DEEPSEEK_BASE_URL`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` (default `deepseek-chat`),
`LLM_TIMEOUT`, `USE_MOCK_LLM` (`src/config.py`, `src/llm/config.py`).

Two governance facts shape this decision:
1. **Healthcare PHI + HIPAA.** Sending PHI to a third-party LLM requires a Business
   Associate Agreement (BAA). Whether a given provider will sign one, and on what terms,
   is a contractual constraint that can force a provider change.
2. **The safety gate is provider-independent.** Because every LLM string already passes
   through the gate (ADR 0001), correctness/safety controls do not depend on which model
   produced the text — so the provider is, in principle, swappable.

Current PHI handling is **downstream masking at the gate**: when `STORE_PHI=False`,
`mask_phi()` scrubs outbound text and stored data ([src/safety/gate.py:323-325](../../src/safety/gate.py#L323-L325)),
and Sentry is configured with `send_default_pii=False` plus breadcrumb scrubbing. There
is **no provider-abstraction interface today** — DeepSeek is effectively hardcoded.

## Decision

1. **Keep `GuardedLLM` as the single mandatory chokepoint** for all model access. No code
   calls a raw client directly (enforced by `tests/test_no_bypass.py` /
   `tests/test_canonical_enforcement.py`).
2. **Introduce a thin provider seam** so the model vendor can change for compliance
   without touching the orchestrator or safety code: a minimal `LLMClient` interface +
   factory selected by config (the unused `LLM_PROVIDER` var is the intended switch),
   with `DeepSeekClient` as the first implementation. This is the **BAA seam** — if PHI
   handling requires a BAA-covered provider (or self-hosted model), only the factory and
   a new client class change.
3. **PHI governance stays defense-in-depth:** `STORE_PHI=False` by default; gate-level
   masking; Sentry PII off. Treat "PHI may leave the building" as gated on a signed BAA
   with the active provider; until then, prefer masking/minimization.

## Consequences

- **+** Provider lock-in is contained behind one seam; compliance-driven swaps are local;
  safety controls are unaffected by the swap.
- **−** The seam is partly aspirational — DeepSeek specifics (temperatures, repair pass)
  live in the client today and must be generalized when a second provider is added.
- **Open item:** BAA/DPA status with DeepSeek is a legal/deployment question
  (`docs/AZURE_CYBERSECURITY_PLAN.md` lists it as outstanding), not something code can
  assert. Do not enable PHI egress to a provider without confirming it.

## Alternatives rejected

- *Hardcode DeepSeek permanently:* rejected — a BAA refusal would force an invasive rewrite.
- *Per-call provider choice in business logic:* rejected — selection belongs at the
  factory/config layer so the safety path stays single and uniform.
