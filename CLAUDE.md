# CLAUDE.md

Project memory for the ORCA Decision Support Assistant. Auto-loaded every session.
Keep it tight — signal over volume. (Version: see [VERSION](VERSION); currently 5.1.0.)

## 1. Product & business context

ORCA is an **AI-powered, multi-vertical voice intake/decision-support platform**
(FastAPI, Python 3.11). It conducts structured intake over the phone (Twilio Voice)
or API and produces a gated, auditable disposition. The business strategy is **one
safety-critical engine grown across many verticals**:

- **Healthcare triage** — the primary, safety-critical workflow (SBAR handoff,
  red-flag escalation). The showcase for the safety architecture.
- **Birchwood collision intake** (`birchwood_collision_intake_v1`) — the **live
  pilot** vertical (automotive collision FNOL-style intake, booking-forward).
- **Insurance FNOL** (`insurance_claims_fnol_v1`) and **property management**
  maintenance — emerging non-clinical verticals.

New verticals are added through a **config/workflow seam**, not by forking the core.
See [docs/decisions/0004-multi-vertical-platform-pattern.md](docs/decisions/0004-multi-vertical-platform-pattern.md).

## 2. Architecture map — who owns what

| Directory | Owns |
|---|---|
| `src/safety/` | The safety gate. `gate.py` = the single entry point for all LLM output (`gate_triage_output`, `gate_outbound_text`); `red_flags.py` + `red_flag_rules.py` = deterministic red-flag engines; `diagnosis_enforcement.py`, `phi_masking.py`, `triage_output_schema.py`. |
| `src/orchestrator/` | Turn loop & clinical core. `orchestrator.py` (pre-check → LLM → post-check → confidence → trace), `validators.py` (JSON repair + `post_check_safety_gate`), `intake_gate.py` (`TransferControlGate`), `schemas.py` (canonical enums, `DecisionTraceEntry`, `ConfidenceBreakdown`), `prompts.py`. |
| `src/protocols/` | `retriever.py` — RAG-lite clinical protocol retrieval. **Supplementary to the LLM only; never overrides rules.** Protocols on disk in `protocols/`. |
| `src/llm/` | LLM access. `deepseek_client.py` (provider), `client.py` (`StructuredLLMClient`, JSON + repair), `guarded_client.py` (`GuardedLLM` — the mandatory wrapper), `config.py`. |
| `src/platform/workflows/` | The vertical seam. `base.py` (`BaseWorkflow` contract), `registry.py` (register/lookup), `router.py` (phone/route → workflow + safety overlays), `spec.py`/`spec_workflow.py` (declarative `WorkflowSpec`). |
| `src/verticals/` | Per-vertical packages: `healthcare/`, `automotive_collision/`, `insurance/`, `property_management/`. Each owns `constants/schemas/rules/extraction/prompts/workflow` (+ `spec` for spec-driven, `completeness` for healthcare). |
| `src/twilio/` | `routes.py` — Twilio Voice webhooks (`/api/v1/voice/incoming|gather|thinking`), TwiML `<Gather>` generation, scripted intake. |
| `src/security/` | `twilio_signature.py` (HMAC-SHA1 webhook validation), `middleware.py`. |
| `src/storage/` | `interface.py` (`StorageInterface` ABC), `memory.py`, `postgres.py`, `factory.py`. |
| `src/observability/` | `logging.py` (structured), `metrics.py`, `sentry_integration.py` (PHI scrubbing). |
| `src/utils/` | `azure_tts.py` (TTS), blob storage, etc. |
| `tests/` | Unit, safety acceptance, `golden_calls/`, `evals/` (DeepEval). |
| `protocols/` | Versioned clinical protocol JSON. |

## 3. Safety invariants (INVIOLABLE)

These are derived from the real safety code. Any change touching safety MUST preserve
all of them. Before editing, read [.claude/skills/modifying-safety-orchestrator/SKILL.md](.claude/skills/modifying-safety-orchestrator/SKILL.md).

1. **Fail-closed hierarchy: RED FLAGS > DETERMINISTIC RULES > PROTOCOL > LLM.**
   A higher tier can override a lower one; never the reverse.
   ([src/safety/gate.py:151-158](src/safety/gate.py#L151-L158), [src/safety/red_flag_rules.py:4-6](src/safety/red_flag_rules.py#L4-L6))
2. **The LLM is never the last word on safety.** Every LLM string reaching a caller,
   file, or DB row passes through `gate_triage_output()` or `gate_outbound_text()`.
   No bypass paths, no second gate. ([src/safety/gate.py:1-11](src/safety/gate.py#L1-L11)) Enforced
   architecturally by `tests/test_no_bypass.py` and `tests/test_canonical_enforcement.py`.
3. **Pre-check gate (deterministic, before any LLM call):** `score_red_flags()` runs
   first every turn. Any CRITICAL flag → `ER_NOW`; weighted score ≥ 10 → `URGENT`;
   both short-circuit with no LLM call. ([src/orchestrator/orchestrator.py:475-635](src/orchestrator/orchestrator.py#L475-L635),
   [src/safety/red_flags.py:285-351](src/safety/red_flags.py#L285-L351))
4. **Post-check gate (after the LLM):** LLM output is scanned for diagnoses, unsafe
   instructions, role/credential claims, and PHI; violations rewrite/replace the text,
   and the LLM **may not downgrade** a prior disposition.
   ([src/orchestrator/validators.py:354-391](src/orchestrator/validators.py#L354-L391), [src/safety/gate.py:265-341](src/safety/gate.py#L265-L341))
5. **Confidence floor:** below the threshold (`confidence_min_threshold` 0.60 in the
   gate; orchestrator escalation threshold 0.60) escalation is forced; a non-emergency
   disposition becomes `HUMAN_REVIEW`. ([src/safety/gate.py:475-489](src/safety/gate.py#L475-L489),
   [src/orchestrator/orchestrator.py:1082-1131](src/orchestrator/orchestrator.py#L1082-L1131))
6. **Weighted-flag no-downgrade:** if weighted flags fired but didn't reach ER_NOW
   (0 < score < 10), the LLM cannot return `SELF_CARE`/`SCHEDULE` — it is upgraded to
   `URGENT`. ([src/safety/gate.py:464-472](src/safety/gate.py#L464-L472))
7. **Confused-caller protocol:** unclear answers trigger a deterministic, non-repetitive
   retry ladder; the **third** unclear answer escalates to a human.
   ([src/orchestrator/orchestrator.py:730-801](src/orchestrator/orchestrator.py#L730-L801))
8. **Decision-trace contract:** every turn appends a `DecisionTraceEntry`
   (user_text, extracted_entities, red_flags/rules_triggered, confidence + breakdown,
   disposition, escalation_required, system_response, protocol hits/citations) and the
   `AuditTrace` records every step. Never drop or weaken trace entries.
   ([src/orchestrator/schemas.py:772-797](src/orchestrator/schemas.py#L772-L797), [src/orchestrator/schemas.py:540-581](src/orchestrator/schemas.py#L540-L581))
9. **Over-escalation is always preferred.** On ANY failure (exception, LLM timeout,
   schema/JSON failure, post-check violation, unknown disposition) the system
   fails closed to `HUMAN_REVIEW`/escalation with a safe fallback message — never to
   reassurance. ([src/safety/gate.py:515-537](src/safety/gate.py#L515-L537), [src/safety/red_flag_rules.py:372-381](src/safety/red_flag_rules.py#L372-L381))
10. **Canonical dispositions only:** `ER_NOW | URGENT | SCHEDULE | SELF_CARE | HUMAN_REVIEW`.
    Unknown values normalize to `HUMAN_REVIEW`. ([src/safety/gate.py:36-75](src/safety/gate.py#L36-L75),
    [src/orchestrator/schemas.py:23-30](src/orchestrator/schemas.py#L23-L30))

> **Do NOT fork the orchestrator or the safety gate.** Extend behavior through the
> workflow/vertical config seam (`src/platform/workflows/` + `src/verticals/`).
> See ADR 0001 and ADR 0004.

## 4. Commands

Run from the repo root with the project venv active. (Full suite excludes server-only
tests per [pytest.ini](pytest.ini): `tests/test_intake_flow.py`, `tests/integration/`, `tests/load/`.)

```bash
# Full test suite (default; excludes the live-server-only dirs above)
python -m pytest tests/ -v --tb=short

# Safety acceptance tests
python -m pytest tests/test_red_flags.py tests/test_phase1_safety.py \
  tests/test_no_bypass.py tests/test_canonical_enforcement.py \
  tests/test_phase5_safety_patch.py -v

# Golden-call regression (deterministic / CI mode — no external LLM)
GOLDEN_CALL_MODE=deterministic_only DISABLE_EXTERNAL_CALLS=1 \
  python -m pytest tests/golden_calls/test_golden_calls.py -v

# Offline simulation runner (no Twilio; --mock = no LLM API)
python -m scripts.simulate_calls --mock

# Lint + format (CI uses ruff 0.15.4 — see .github/workflows/ci.yml)
ruff check src/ tests/
ruff format --check src/ tests/

# Security scans (what CI runs)
gitleaks detect --source . --config .gitleaks.toml --redact --no-banner -v
bandit -r src/ -c pyproject.toml --severity-level high --confidence-level high
pip-audit -r requirements.txt
```

## 5. Secrets / .env policy

- **Never commit secrets.** All keys/tokens come from env vars or a gitignored `.env`.
  `.env.example` is the committed template (the only `.env*` file allowed in git).
- `.gitignore` blocks `.env*`, `secrets/`, `*.pem|key|p12|pfx`; pre-commit hooks
  (`no-env-files`, `no-private-keys`) and gitleaks block them too ([.pre-commit-config.yaml](.pre-commit-config.yaml)).
- **Merge-blocking CI jobs** (`.github/workflows/ci.yml`, run in order): `gitleaks` →
  `lint` (ruff) → `test` (pytest), `healthcare-evals` (DeepEval), `security-scan`
  (bandit high/high + pip-audit). A weekly full-history gitleaks runs in `secret-scan.yml`.
- See [SECURITY.md](SECURITY.md), [docs/decisions/0003-llm-provider-abstraction-and-phi-governance.md](docs/decisions/0003-llm-provider-abstraction-and-phi-governance.md).

## 6. Before you touch X, read skill Y

| If you are changing… | Read first |
|---|---|
| `src/safety/`, `src/orchestrator/`, gates, confidence, decision trace, red flags | [.claude/skills/modifying-safety-orchestrator/SKILL.md](.claude/skills/modifying-safety-orchestrator/SKILL.md) |
| Adding/extending a vertical or workflow | [.claude/skills/adding-a-vertical/SKILL.md](.claude/skills/adding-a-vertical/SKILL.md) + [docs/templates/vertical-checklist.md](docs/templates/vertical-checklist.md) |
| Twilio/voice, STT/TTS, telephony routes | [.claude/skills/voice-pipeline-operations/SKILL.md](.claude/skills/voice-pipeline-operations/SKILL.md) |
| LLM provider, prompts, PHI governance | [docs/decisions/0003-llm-provider-abstraction-and-phi-governance.md](docs/decisions/0003-llm-provider-abstraction-and-phi-governance.md) |
| Terminology (disposition, SBAR, FNOL, red flag…) | [docs/GLOSSARY.md](docs/GLOSSARY.md) |
| Contributing conventions, commit/PR discipline | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Installing external/third-party skills | [docs/SKILLS.md](docs/SKILLS.md) |

## 7. Hard rule for AI agents

Safety logic is **additive-only**: do not loosen a gate, remove a fail-closed path, or
let the LLM gain final authority. Any safety change ships with **new escalation tests**
and must keep the full suite + safety acceptance tests green. When the code is
ambiguous, escalate the question to a human — do not guess.
