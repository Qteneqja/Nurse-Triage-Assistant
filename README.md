# ORCA Decision Support Assistant Phone Tool

[![CI](https://github.com/Qteneqja/Nurse-Triage-Assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Qteneqja/Nurse-Triage-Assistant/actions/workflows/ci.yml)

ORCA is an AI-powered, **multi-vertical voice intake and decision-support
platform** (FastAPI, Python 3.11+, version [5.1.0](VERSION)). It conducts
structured intake over the phone (Twilio Voice) or API and produces a gated,
auditable disposition. One safety-critical engine is grown across verticals:

| Vertical | Workflow ID | Status |
|---|---|---|
| **Healthcare triage** | `healthcare_triage_v1` | Primary safety-critical workflow (SBAR handoff, red-flag escalation) |
| **Automotive collision** | `birchwood_collision_intake_v1` | **Live pilot** — Birchwood Collision intake, booking-forward |
| **Insurance FNOL** | `insurance_claims_fnol_v1` | Demo-ready |
| **Property management** | maintenance intake | Emerging |

New verticals are added through a **config/workflow seam**
(`src/platform/workflows/` + `src/verticals/`), never by forking the core —
see [ADR 0004](docs/decisions/0004-multi-vertical-platform-pattern.md). For a
categorized map of operational, security, pilot, platform, and evaluation
documents, see [`docs/README.md`](docs/README.md).

## Secrets & Security

- **Secrets must never be committed** to this repository. All API keys, tokens, and credentials must be provided via environment variables or a `.env` file (which is `.gitignore`d).
- **All configuration** is managed through environment variables — see `.env.example` for the full list.
- **See [`SECURITY.md`](SECURITY.md)** for the secrets management policy and rotation procedures.
- **See [`SECURITY_POSTURE.md`](SECURITY_POSTURE.md)** for GitHub security settings and incident response playbook.
- **See [`SECURITY_CLEANUP.md`](SECURITY_CLEANUP.md)** for the security audit report and remediation steps.
- Twilio webhooks are HMAC-SHA1 signature-validated (`src/security/twilio_signature.py`); the API layer applies rate limiting and safe error handling (`src/security/middleware.py`).

## Pilot Documentation

- [`PILOT_READINESS.md`](PILOT_READINESS.md) — Pilot gate checklist
- [`PILOT_ESCALATION_WORKFLOW.md`](PILOT_ESCALATION_WORKFLOW.md) — How the system escalates to humans
- [`PILOT_SYSTEM_LIMITATIONS.md`](PILOT_SYSTEM_LIMITATIONS.md) — What the system does and does NOT do
- [`PILOT_SUCCESS_METRICS.md`](PILOT_SUCCESS_METRICS.md) — Measurable success criteria
- [`STAGING_RUNBOOK.md`](STAGING_RUNBOOK.md) — Azure staging environment operational reference
- [`docs/BIRCHWOOD_CALL_TEST_CHECKLIST.md`](docs/BIRCHWOOD_CALL_TEST_CHECKLIST.md) — live-call validation pack
- [`docs/BIRCHWOOD_TEAM_TEST_PACK.md`](docs/BIRCHWOOD_TEAM_TEST_PACK.md) — hands-on team test pack (no technical setup)

## Architecture

```
Phone call (Twilio)
  │
  ├── VOICE_PIPELINE=gather (default) ──► src/twilio/routes.py
  │     /incoming → greeting → <Gather> per turn
  │     /gather   → scripted stages OR background workflow turn
  │     /thinking → hold loop (verbal filler → typing bed) until the turn +
  │                 reply audio are ready, then delivers the response
  │
  ├── VOICE_PIPELINE=conversation_relay ──► src/twilio/conversation_relay.py
  │     streaming WebSocket transport (same workflow engine, same gates)
  │
  ▼
Workflow engine (src/platform/workflows/)
  router → registry → vertical workflow (src/verticals/<vertical>/)
  │   Platform overlays apply to EVERY non-clinical turn (injury advisory,
  │   record flagging) no matter what the workflow returns.
  │
  ├── Healthcare ──► Orchestrator (src/orchestrator/orchestrator.py)
  │     1. Pre-check: score_red_flags() — deterministic, BEFORE any LLM call
  │        (critical flag → ER_NOW, weighted score ≥ 10 → URGENT, no LLM)
  │     2. Protocol retrieval (RAG-lite, supplementary only)
  │     3. Single gated LLM call (GuardedLLM → gate_triage_output)
  │     4. Post-check: diagnosis/unsafe-instruction/PHI scan, no downgrades
  │     5. Confidence floor (< 0.60 → forced escalation)
  │     6. Decision trace appended EVERY turn
  │
  └── Birchwood collision ──► deterministic scripted intake (default) or the
        gated-LLM conversational tier (BIRCHWOOD_CONVERSATIONAL_INTAKE=true).
        Disposition is ALWAYS deterministic (classify_collision_intake);
        the LLM only converses and extracts.
```

**Speech:** Twilio STT (`speechModel="phone_call"`, enhanced) on the gather
path; Deepgram/Google on ConversationRelay. **Voice:** Azure Neural TTS
(Bree DragonHD by default, per-vertical profiles, expressive SSML for the
Birchwood "Aurora" persona) with automatic Polly fallback when Azure is
unavailable.

### Perceived-latency design (gather path)

The caller should never sit in dead air:

- **Dynamic turns** run the workflow/LLM in a background task. The caller
  hears ONE short spoken filler ("Okay, one sec." — pre-rendered at startup
  when `AZURE_SPEECH_KEY` is set), then a quiet keyboard-typing bed while the
  turn completes. The reply's TTS audio is **pre-warmed inside the background
  task**, so delivery is immediate when the turn finishes.
- **Birchwood scripted turns** synthesize reply audio *behind the same hold
  loop* whenever it is not already cached, instead of as silence inside the
  webhook.
- The hold loop **fails closed** at a cycle cap (~60s): the task is
  cancelled and the caller gets the vertical-appropriate apology + callback
  promise rather than endless hold audio.
- Duplicate/redelivered webhooks replay the previously delivered response
  idempotently; a lost in-flight turn replays the last question instead of
  asking the caller to repeat themselves.
- Deterministic de-stutter: adjacent near-duplicate sentences in a reply are
  collapsed (safety sentences are never dropped), so the assistant never
  says the same thing twice in slightly different words.

### Key Components

- **Safety gate** (`src/safety/gate.py`): the single entry point for ALL LLM output (`gate_triage_output`, `gate_outbound_text`). No bypass paths — enforced architecturally by `tests/test_no_bypass.py` and `tests/test_canonical_enforcement.py`.
- **Deterministic red-flag engines** (`src/safety/red_flags.py`, `src/safety/red_flag_rules.py`): pattern-match utterance + state, run before the LLM, cannot be overridden.
- **Orchestrator** (`src/orchestrator/`): turn loop and clinical core — pre-check → LLM → post-check → confidence → decision trace.
- **Guarded LLM client** (`src/llm/guarded_client.py` wrapping `src/llm/client.py`): DeepSeek provider behind an abstraction ([ADR 0003](docs/decisions/0003-llm-provider-abstraction-and-phi-governance.md)), JSON schema validation with automatic repair, retries, and mandatory gating.
- **Workflow platform** (`src/platform/workflows/`): registry, phone-number routing, safety overlays, and a declarative `WorkflowSpec` engine — a new vertical needs one JSON definition plus config ([docs/WORKFLOW_ENGINE.md](docs/WORKFLOW_ENGINE.md)).
- **Protocol retrieval** (`src/protocols/retriever.py`): RAG-lite clinical protocol grounding — supplementary to the LLM only, never overrides rules. 8 versioned protocols in `protocols/v1/`.
- **Storage abstraction** (`src/storage/`): `StorageInterface` with in-memory (dev) and PostgreSQL (staging/production, enforced) backends; Alembic migrations.
- **Staff dashboard** (`/dashboard/records`): records list with injury/urgent pinning, filters, record detail (shop summary, transcript, collision data), audited status transitions, and a Birchwood pitch view at `/dashboard/birchwood` ([docs/DASHBOARD_RECORDS.md](docs/DASHBOARD_RECORDS.md)).
- **Observability** (`src/observability/`): structured JSON logging, metrics counters/histograms at `/metrics`, Sentry with PHI scrubbing ([MONITORING.md](MONITORING.md)).

## Safety Invariants (inviolable)

The full list with code references lives in [CLAUDE.md](CLAUDE.md) §3 and the
[safety skill](.claude/skills/modifying-safety-orchestrator/SKILL.md). In brief:

```
RED FLAGS  >  DETERMINISTIC RULES  >  PROTOCOL  >  LLM
```

1. **The LLM is never the last word on safety.** Every LLM string that reaches a caller, file, or DB row passes through the safety gate.
2. **Pre-check gate:** deterministic red-flag scoring runs before any LLM call; critical flags short-circuit to `ER_NOW` with no LLM involvement.
3. **Post-check gate:** LLM output is scanned for diagnoses, unsafe instructions, role/credential claims, and PHI; the LLM may never downgrade a prior disposition.
4. **Confidence floor:** below 0.60, escalation is forced.
5. **Fail closed, always:** any exception, timeout, schema failure, or unknown value escalates to `HUMAN_REVIEW` with a safe message — never to reassurance. Over-escalation is always preferred.
6. **Canonical dispositions only:** `ER_NOW | URGENT | SCHEDULE | SELF_CARE | HUMAN_REVIEW`. Unknown values normalize to `HUMAN_REVIEW`.
7. **Confused-caller protocol:** unclear answers get a deterministic, non-repetitive retry ladder; the **third** consecutive unclear answer escalates to a human.
8. **Decision-trace contract:** every turn appends a `DecisionTraceEntry` (entities, flags, confidence breakdown, disposition, protocol citations); the audit trace records every step.
9. **Non-clinical injury reflex:** any injury mention on a non-clinical call produces a spoken 911/medical-attention advisory (exactly once) and flags the record for human review — on every routing outcome, including declines.

Safety logic is **additive-only**: gates are never loosened, and every safety
change ships with new escalation tests.

## Healthcare Triage Flow

1. Greeting and disclaimer, then scripted intake (name, age, sex, chief complaint)
2. Dynamic symptom questioning driven by the orchestrator (stop conditions: confidence ≥ 0.75, max 12 turns, or no missing fields)
3. Deterministic red-flag detection on every turn (chest pain, breathing difficulty, stroke signs, anaphylaxis, suicidal intent, …)
4. Finalization: disposition + patient summary + clinician **SBAR** (Situation-Background-Assessment-Recommendation)
5. Optional warm transfer to a nurse line (`NURSE_TRANSFER_NUMBER`)

**Outputs:** caller-facing plain summary, clinician SBAR, and structured JSON
(symptoms, flags, disposition, decision trace) for EHR/audit use.

## Birchwood Collision Intake (live pilot)

- **Persona "Aurora"** with a deterministic naturalness pass: per-call phrasing variants, backchannels, slot echoes of what the caller said, verbal hold fillers, and off-topic redirection — all hand-authored copy, no LLM required.
- **Default tier — deterministic scripted flow:** narrative-first ("walk me through what happened"), multi-segment story capture that never cuts callers off, deterministic extraction prefills fields the story answered, targeted gap-fill of the missing required fields, readback confirmation with a correction path, and a "here's what happens next" close.
- **Premium tier — conversational intake** (`BIRCHWOOD_CONVERSATIONAL_INTAKE=true`): a gated-LLM natural conversation collects the same required fields out of order (name, phone, spelled email, vehicle year/make/model, damage, plate, preferred location, MPI claim details), answers caller questions about Birchwood, wraps up with a Q&A offer, then closes. The **disposition stays deterministic** and every utterance passes the outbound-text gate.
- **Deterministic routing outcomes:** `COMPLETED_INTAKE`, `INCOMPLETE_CALLBACK_NEEDED`, `TRANSFER_COLLISION_CENTER`, `TRANSFER_GLASS_DEPARTMENT`, private-pay and missing-claim flags — via `classify_collision_intake`.
- **Records** flow to the staff dashboard with shop summaries (the collision analogue of SBAR), BI data points extracted from the damage description, and audited status transitions.
- Caller says "transfer" (or presses 0) at any time → immediate human handoff.

## Privacy & Data Handling

- **Storage:** in-memory for development; **PostgreSQL is enforced in staging/production** (sessions survive restarts; the platform validates this at startup).
- **PHI controls:** `STORE_PHI=false` redacts user/system text at rest; PHI masking applies to logs, Sentry events, and dashboard free text.
- **Human review fallback:** uncertain or high-risk cases are always flagged to a person.
- **HIPAA considerations:** designed with healthcare privacy regulations in mind; see [ADR 0003](docs/decisions/0003-llm-provider-abstraction-and-phi-governance.md) for PHI governance across LLM providers.

## Tech Stack

- **Backend:** FastAPI (async) on Python 3.11+
- **LLM:** DeepSeek behind `StructuredLLMClient`/`GuardedLLM` (provider-swappable, JSON-validated, always gated)
- **Voice:** Twilio Voice (gather/TwiML default; ConversationRelay WebSocket behind `VOICE_PIPELINE`), Azure Neural TTS with Polly fallback
- **Storage:** SQLAlchemy 2.0 + PostgreSQL (Alembic migrations) / in-memory for dev
- **Evals:** DeepEval healthcare eval suite runs in CI (`tests/evals/`)
- **Deploy:** Docker → Azure Container Apps (auto-deploy on merge to `main` after CI passes)

## CI/CD Pipeline

Merge-blocking GitHub Actions jobs (`.github/workflows/ci.yml`), in order:

1. **Gitleaks** secret scan (custom rules in `.gitleaks.toml`) — a leak stops the whole pipeline
2. **Lint:** `ruff check` + `ruff format --check`
3. **Test suite:** full `pytest` with coverage (excludes live-server/integration/load dirs per `pytest.ini`)
4. **Healthcare evals:** DeepEval suite over `tests/evals/`
5. **Security scan:** `bandit` (high severity/confidence) + `pip-audit`

Plus [`secret-scan.yml`](.github/workflows/secret-scan.yml): every push on every
branch, and a weekly full-history gitleaks backstop (history was rewritten
2026-06-10 to purge previously committed secrets — see
[docs/HISTORY_REWRITE_PROCEDURE.md](docs/HISTORY_REWRITE_PROCEDURE.md)).

**Auto-deploy:** on a successful CI run on `main`, the AutoDeployTrigger
workflow builds the image and updates the Azure Container App
(single-worker uvicorn; see [DEPLOYMENT.md](DEPLOYMENT.md)).

## Commands

Run from the repo root with the project venv active.

```bash
# Full test suite (default; excludes server-only dirs per pytest.ini)
python -m pytest tests/ -v --tb=short

# Safety acceptance tests
python -m pytest tests/test_red_flags.py tests/test_phase1_safety.py \
  tests/test_no_bypass.py tests/test_canonical_enforcement.py \
  tests/test_phase5_safety_patch.py -v

# Golden-call regression (deterministic / CI mode — no external LLM):
#   30 healthcare + 10 Birchwood + 3 insurance cases
GOLDEN_CALL_MODE=deterministic_only DISABLE_EXTERNAL_CALLS=1 \
  python -m pytest tests/golden_calls/test_golden_calls.py -v

# Offline simulation runners (no Twilio; --mock = no LLM API)
python -m scripts.simulate_calls --mock                 # healthcare scenarios
python -m scripts.simulate_birchwood_call               # Birchwood conversation
python -m scripts.run_insurance_demo                    # insurance FNOL demo

# Voice latency measurement (app-side overhead; see ADR 0002 for the gate)
python -m scripts.measure_voice_latency --runs 30

# Lint + format (CI parity)
ruff check src/ tests/ && ruff format --check src/ tests/

# Security scans (what CI runs)
gitleaks detect --source . --config .gitleaks.toml --redact --no-banner -v
bandit -r src/ -c pyproject.toml --severity-level high --confidence-level high
pip-audit -r requirements.txt
```

## Quick Start

### Prerequisites
- Python 3.11+
- DeepSeek API key (set in `.env`)
- Twilio account (for voice calls); Azure Speech + Blob Storage (for the neural voice)

### Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

pip install -r requirements.txt

cp .env.example .env
# Edit .env and add your DEEPSEEK_API_KEY (and Twilio/Azure keys as needed)
```

### Run the Server

```bash
python run.py
# Server starts at http://127.0.0.1:8000
#   GET /health   — liveness probe
#   GET /ready    — readiness probe (DB connectivity)
#   GET /metrics  — metrics snapshot
#   /dashboard/records — staff dashboard (token auth)
```

> **Virtual environments are not portable.** If pytest fails with
> *"Unable to create process"*, delete `.venv` and recreate it in place.
> `tests/test_intake_flow.py`, `tests/integration/`, and `tests/load/` need a
> live server and are excluded from the default run by `pytest.ini`.

### Docker

```powershell
docker-compose up --build          # api (port 8000) + postgres 15
```

## Key Environment Variables

See `.env.example` for the complete annotated list. The load-bearing ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `STORAGE_BACKEND` | `memory` | `memory` or `postgres` (postgres enforced in staging/production) |
| `STORE_PHI` | `false` | Whether to persist user/system text (PHI) |
| `VOICE_PIPELINE` | `gather` | Voice transport: `gather` or `conversation_relay` (rollback = set back to `gather`) |
| `BIRCHWOOD_CONVERSATIONAL_INTAKE` | `false` | Birchwood premium conversational tier (gated LLM) vs deterministic scripted flow |
| `BIRCHWOOD_SHORT_FIELD_SPEECH_TIMEOUT` | `3` | Twilio speechTimeout for Birchwood short answers (`auto` = snappier adaptive end-pointing) |
| `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` | — | Azure neural TTS (absent → Polly fallback; also gates the pre-rendered verbal hold fillers) |
| `TWILIO_VALIDATE_SIGNATURE` | on in staging/prod | Webhook HMAC validation |
| `CONFIDENCE_MIN_THRESHOLD` | `0.60` | Confidence floor below which the system escalates |
| `REDFLAG_SCORE_THRESHOLD` | `10` | Weighted red-flag score at/above which pre-check escalates |
| `ENRICHMENT_ENABLED` | `false` | Shadow-mode post-call enrichment (off-call-path, fails closed) |
| `RATE_LIMIT` | `60/minute` | API rate limit per client IP |

## Project Structure

```
src/
  safety/                 # THE safety gate + deterministic red-flag engines
    gate.py               #   single entry point for all LLM output
    red_flags.py          #   pre-check scoring (runs before any LLM call)
    red_flag_rules.py     #   deterministic rules engine
    injury_detection.py   #   non-clinical injury reflex (Invariant 3)
    phi_masking.py, diagnosis_enforcement.py, triage_output_schema.py
  orchestrator/           # Turn loop & clinical core
    orchestrator.py       #   pre-check → LLM → post-check → confidence → trace
    validators.py         #   JSON repair + post_check_safety_gate
    intake_gate.py        #   TransferControlGate
    schemas.py, prompts.py
  llm/                    # Provider abstraction (ADR 0003)
    client.py             #   StructuredLLMClient (JSON + repair + retries)
    guarded_client.py     #   GuardedLLM — the mandatory gated wrapper
    deepseek_client.py, config.py
  platform/workflows/     # The vertical seam (ADR 0004)
    base.py, registry.py, router.py (routing + safety overlays), spec.py
  verticals/              # Per-vertical packages
    healthcare/           #   constants/schemas/rules/extraction/completeness
    automotive_collision/ #   Birchwood: workflow, rules, conversational_intake,
                          #   voice_naturalness (Aurora), narrative_extraction
    insurance/            #   FNOL intake
    property_management/  #   maintenance intake
  twilio/                 # Voice transports
    routes.py             #   gather webhooks: /incoming /gather /thinking + hold loop
    conversation_relay.py #   streaming WebSocket transport (VOICE_PIPELINE flag)
    webhook_stability.py  #   idempotent replay, silence caps, narrative capture
  protocols/retriever.py  # RAG-lite protocol retrieval (supplementary only)
  storage/                # StorageInterface: memory + postgres + factory
  api/                    # REST intake endpoints + staff dashboard (src/api/dashboard.py)
  enrichment/             # Shadow-mode post-call enrichment (flagged off)
  observability/          # Structured logging, metrics, Sentry (PHI-scrubbed)
  security/               # Twilio signature validation, middleware
  utils/                  # azure_tts, voice_fillers, typing_sound, blob storage
protocols/v1/             # 8 versioned clinical protocol JSONs (governed)
alembic/                  # Database migrations
tests/                    # ~1,000 tests: unit, safety acceptance, golden_calls/,
                          #   evals/ (DeepEval), vertical packs
scripts/                  # simulate_calls, simulate_birchwood_call, demo runners,
                          #   measure_voice_latency, pilot_metrics, seeders
docs/                     # decisions/ (ADRs), pilot/, runbooks, GLOSSARY
.claude/skills/           # Working agreements for safety, verticals, voice ops
```

## Development Milestones

| Milestone | What it added |
|---|---|
| Phase 1 — Clinical core | Deterministic fail-closed safety architecture: pre/post gates, confidence scoring, confused-caller ladder, decision trace |
| Phase 2 — Protocol grounding | RAG-lite retrieval over 8 versioned clinical protocols with decision-trace citations |
| Phase 3 — Operational readiness | Postgres + Alembic, structured logging/metrics, governance gating, rate limiting, Docker |
| Phases 4–12 | Azure deployment, Twilio hardening, healthcare dynamic intake, staff dashboard |
| Phases 13–14 | Insurance FNOL + automotive collision verticals via the workflow seam |
| Pilot PRs 1–5 | Webhook idempotency, narrative-first Birchwood flow, declarative workflow engine, dashboard MVP, pilot gate + docs |
| Voice/UX track | ConversationRelay transport (flagged), Aurora naturalness pass, conversational premium tier, wrap-up Q&A, perceived-latency hold system + de-stutter |

## Monitoring & Error Tracking

Production uses Sentry with full PHI scrubbing — see [`MONITORING.md`](MONITORING.md)
for configuration, the 3-layer PHI safeguard architecture, and event boundaries.

## Contact & Licensing

This proprietary software is protected by copyright. For licensing inquiries, partnership opportunities, or demonstrations, please contact the development team.

**Note:** This is a commercial technology product. Unauthorized reproduction or use is prohibited.

---

**Disclaimer:** This software is for demonstration and research purposes. It is not FDA-approved and should not be used as a substitute for professional medical advice, diagnosis, or treatment. Always seek the advice of qualified health providers with questions about medical conditions.

**Copyright © 2026. All rights reserved.**
