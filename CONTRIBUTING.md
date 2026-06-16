# Contributing to ORCA

ORCA is a safety-critical, multi-vertical voice intake platform. Read [CLAUDE.md](CLAUDE.md)
first for the architecture and safety invariants. This file covers conventions and the
discipline for getting a change merged.

## Golden rule

> **Safety logic is additive-only.** Never loosen a gate, remove a fail-closed path, or
> give the LLM final authority. Every change to `src/safety/` or `src/orchestrator/`
> ships with **new escalation tests** and must keep the full suite green. When the code
> is ambiguous, escalate to a human — do not guess. See
> [.claude/skills/modifying-safety-orchestrator/SKILL.md](.claude/skills/modifying-safety-orchestrator/SKILL.md).

## Code conventions

- **Python 3.11.** Match the style of the surrounding module.
- **Pydantic v2** for all data models (schemas, LLM I/O, records). Use `model_validate` /
  `model_dump`; validate LLM output against a schema — never trust raw JSON.
- **Structured logging** via `src/observability/logging.py` (`set_log_context` /
  `clear_log_context`). No `print`. Never log PHI; respect `STORE_PHI` and `mask_phi`.
- **Decision-trace entries** are mandatory: any new path through the orchestrator turn
  must append a `DecisionTraceEntry` and `AuditTrace` step
  ([src/orchestrator/schemas.py](src/orchestrator/schemas.py)). Do not add a path that
  produces a caller-facing outcome without a trace.
- **Storage** goes through `StorageInterface` ([src/storage/interface.py](src/storage/interface.py)).
  Don't reach into a concrete backend; add methods to the interface so memory + postgres
  stay in parity.
- **LLM access** only through `GuardedLLM` ([src/llm/guarded_client.py](src/llm/guarded_client.py)).
  Never import or call a raw client directly (`tests/test_no_bypass.py` enforces this).
- **Canonical dispositions only:** `ER_NOW | URGENT | SCHEDULE | SELF_CARE | HUMAN_REVIEW`
  (`tests/test_canonical_enforcement.py` enforces this).
- **New verticals** follow the seam — see
  [.claude/skills/adding-a-vertical/SKILL.md](.claude/skills/adding-a-vertical/SKILL.md)
  and [docs/templates/vertical-checklist.md](docs/templates/vertical-checklist.md). Do not
  fork the orchestrator.

## Secrets

Never commit secrets. Use env vars / a gitignored `.env`; `.env.example` is the template.
Pre-commit hooks + gitleaks block `.env*` and key files. Install hooks:
`pip install pre-commit && pre-commit install`.

## Test-before-merge discipline

Run before opening a PR (from repo root, venv active):

```bash
ruff check src/ tests/ && ruff format --check src/ tests/
python -m pytest tests/ -v --tb=short
# Safety acceptance + golden-call regression for any safety/orchestrator change:
python -m pytest tests/test_red_flags.py tests/test_phase1_safety.py \
  tests/test_no_bypass.py tests/test_canonical_enforcement.py \
  tests/test_phase5_safety_patch.py -v
GOLDEN_CALL_MODE=deterministic_only DISABLE_EXTERNAL_CALLS=1 \
  python -m pytest tests/golden_calls/test_golden_calls.py -v
bandit -r src/ -c pyproject.toml --severity-level high --confidence-level high
```

CI (`.github/workflows/ci.yml`) gates merges on, in order: **gitleaks → lint (ruff) →
test (pytest), healthcare-evals (DeepEval), security-scan (bandit + pip-audit)**. All must
pass. The full suite must pass **unchanged** — fix the code, not the test, when an
invariant test fails.

## Commit / PR expectations

- **Small, focused commits**, each with a clear message describing the *what* and *why*.
- Branch off `main`; do not commit directly to `main`.
- PR description states: what changed, why, which tests you ran, and — for any
  safety/orchestrator change — **whether escalation coverage changed** (it should not
  regress) and which new escalation tests you added.
- Update `CLAUDE.md`, the relevant skill, or an ADR when you change architecture or an
  invariant. Add an ADR (see [docs/decisions/](docs/decisions/)) for durable decisions.
- Never weaken a safety test or remove a fail-closed branch to make CI pass.
