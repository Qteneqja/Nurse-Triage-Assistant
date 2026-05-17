# Nurse Triage Assistant

[![CI](https://github.com/Qteneqja/Nurse-Triage-Assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Qteneqja/Nurse-Triage-Assistant/actions/workflows/ci.yml)

An AI-powered multi-vertical voice decision-support platform that conducts structured intake through phone calls (Twilio Voice) or API integrations. Healthcare triage remains the primary safety-critical workflow, while property management maintenance and insurance first notice of loss (FNOL) intake are emerging non-clinical verticals.

## Secrets & Security

- **Secrets must never be committed** to this repository. All API keys, tokens, and credentials must be provided via environment variables or a `.env` file (which is `.gitignore`d).
- **All configuration** is managed through environment variables — see `.env.example` for the full list.
- **See [`SECURITY_CLEANUP.md`](SECURITY_CLEANUP.md)** for the detailed security audit report and remediation steps.
- **See [`SECURITY.md`](SECURITY.md)** for the secrets management policy and rotation procedures.
- **See [`SECURITY_POSTURE.md`](SECURITY_POSTURE.md)** for GitHub security settings and incident response playbook.

## Pilot Documentation

- [`PILOT_READINESS.md`](PILOT_READINESS.md) — Pilot gate checklist and git history assessment
- [`PILOT_ESCALATION_WORKFLOW.md`](PILOT_ESCALATION_WORKFLOW.md) — How the system escalates to human clinicians
- [`PILOT_SYSTEM_LIMITATIONS.md`](PILOT_SYSTEM_LIMITATIONS.md) — What the system does and does NOT do
- [`PILOT_SUCCESS_METRICS.md`](PILOT_SUCCESS_METRICS.md) — Measurable success criteria for 2–4 week pilot
- [`STAGING_RUNBOOK.md`](STAGING_RUNBOOK.md) — Azure staging environment operational reference
- [`STAGING_MANUAL_TEST_PACK.md`](STAGING_MANUAL_TEST_PACK.md) — 10-call manual validation checklist

## Overview

Designed for hospitals, nurse triage lines (like Health Links in Winnipeg), and healthcare providers to:
- Reduce wait times and ER congestion
- Automate initial intake before clinician review  
- Provide structured, consistent patient assessments
- Generate SBAR-formatted handoff summaries for healthcare professionals

The platform workflow layer also supports non-clinical verticals. Phase 13 adds
the insurance FNOL foundation with workflow ID `insurance_claims_fnol_v1` and
placeholder local route `INSURANCE_FNOL_PHONE_NUMBER=+15555550130`.

- Insurance FNOL demo pack with scripted sample claims, expected extraction
  JSON, transcripts, and broker-facing demo materials.

## Architecture

```
Phone Call (Twilio)
  │
  ▼
FastAPI Backend (src/twilio/routes.py)
  │
  ├── Scripted Stages: NAME → AGE → SEX → CHIEF_COMPLAINT
  │
  └── DYNAMIC Stage ──► Orchestrator (src/orchestrator/)
                            │
                            ├── 1. Deterministic Safety Rules (src/safety/red_flags.py)
                            │     └── Pattern-match utterance + state → escalate immediately if triggered
                            │
                            ├── 2. Single LLM Call (DeepSeek via src/llm/client.py)
                            │     └── Intake extraction + next question + lightweight safety
                            │
                            ├── 3. State Update → Check stop conditions
                            │     └── confidence ≥ 0.75, max turns (12), or no missing fields
                            │
                            └── 4. Finalization (1 LLM call)
                                  └── Disposition + SBAR + Patient Summary
```

### Key Components

- **Voice Intake:** Twilio Voice API with speech recognition and TwiML responses
- **Backend API:** FastAPI with session-based intake management
- **Multi-Agent Orchestrator:** Logical agents (Intake, Safety, Clinical Reasoning, SBAR) coordinated by `src/orchestrator/orchestrator.py`
- **Deterministic Safety Engine:** Regex-based red-flag detection (`src/safety/red_flags.py`) — always runs before LLM, cannot be overridden
- **Structured LLM Client:** DeepSeek wrapper with Pydantic validation plus automatic repair (`src/llm/client.py`)
- **Storage Abstraction:** Swappable session storage via `StorageInterface` — in-memory MVP, Redis-ready
- **Workflow Platform:** Registered vertical workflows for healthcare,
  property management, and insurance FNOL intake

## Features

### Intake Protocol
1. Greeting and medical disclaimer
2. Patient information collection (name, age, sex)
3. Chief complaint capture
4. Dynamic symptom questioning based on complaint
5. Red flag detection (chest pain, breathing difficulty, severe bleeding, etc.)
6. Duration and severity assessment

### Triage Dispositions
- **SAFE:** Self-care advice appropriate
- **PCP:** Schedule with primary care physician
- **URGENT:** Urgent care or ER within hours
- **EMERGENCY:** Immediate 911/ER instruction
- **HUMAN_REVIEW:** Unclear case requiring nurse review

### Output
- **Patient Summary:** Plain-language recap of conversation
- **Clinician SBAR:** Situation-Background-Assessment-Recommendation format
- **Structured Data:** JSON session summaries with symptoms, flags, and disposition

## Tech Stack

- **Backend:** FastAPI with async/await architecture
- **AI/ML:** Advanced LLM integration for natural language understanding and clinical reasoning
- **Voice:** Twilio Voice API with real-time speech recognition
- **Storage:** Secure, ephemeral session management (no persistent PHI)
- **Development:** Modern Python stack with production-ready frameworks

## Key Benefits

### For Healthcare Providers
- **Reduce Wait Times:** Automate initial intake to free up nurse capacity
- **Consistent Quality:** Every patient receives the same thorough assessment
- **24/7 Availability:** Accept calls and intake requests around the clock
- **Cost Effective:** Lower operational costs while maintaining care quality
- **Scalable:** Handle high call volumes without additional staff

### For Patients
- **Immediate Response:** No more waiting on hold
- **Clear Guidance:** Receive appropriate care recommendations
- **Accessible:** Phone-based system works for all demographics
- **Safe:** Emergency conditions are immediately identified and escalated

### Clinical Features
- **SBAR Format:** Industry-standard handoff summaries for clinicians
- **Red Flag Detection:** Automatic identification of emergency symptoms
- **Structured Data:** Organized symptom capture for EHR integration
- **Audit Trail:** Complete session transcripts and reports

## Privacy & Safety

- **No Persistent PHI:** Sessions are stored in-memory only, cleared on server restart
- **Ephemeral Storage:** No long-term patient data retention
- **Human Review Fallback:** Uncertain or high-risk cases flagged for nurse review
- **Emergency Detection:** Immediate escalation for life-threatening symptoms
- **HIPAA Considerations:** Designed with healthcare privacy regulations in mind
- **Secure Communications:** End-to-end encrypted voice and data transmission

## Use Cases

### Health Links / Nurse Triage Lines
Handle high call volumes during flu season or public health emergencies. Collect initial information before nurse callback, reducing time-to-triage.

### Emergency Departments
Pre-screen patients before arrival. Identify true emergencies vs. primary care needs, helping manage ER capacity.

### Telemedicine Platforms
Automate patient intake before virtual consultations. Provide doctors with structured pre-visit assessments.

### Urgent Care Centers
Phone-ahead triage to prepare for patient arrival. Optimize resource allocation and wait times.

## Phase 1 Safety Guarantees

Phase 1 ("Clinical Core") implements deterministic, fail-closed safety architecture **in addition to** the existing red-flag rules. Every call path passes through at least two independent safety layers before any LLM output can affect the caller.

### Safety Priority Order

```
RED FLAGS  >  DETERMINISTIC RULES  >  PROTOCOL  >  LLM
```

The LLM is **never** the last word on patient safety. Any violation detected at a higher layer overrides the LLM completely.

### Pre-Check Safety Gate

Runs **before every LLM call** using `score_red_flags()` in `src/safety/red_flags.py`:

| Result | Condition | LLM called? |
|--------|-----------|-------------|
| `ER_NOW` | Any critical flag triggered (cardiac arrest, anaphylaxis, stroke, severe breathing failure, uncontrolled bleeding, loss of consciousness, suicidal intent) | ❌ No |
| `URGENT` | Weighted red-flag score ≥ 10 | ❌ No |
| `UNDECIDED` | Score < 10, no critical flags | ✅ Yes |

### Post-Check Safety Gate

Runs **after every LLM output** via `post_check_safety_gate()` in `src/orchestrator/validators.py`. Raises `PostCheckViolation` if the LLM:

- States a diagnosis (e.g., "You have pneumonia")
- Gives unsafe clinical instructions (e.g., "You don't need to go to the ER")
- Attempts to downgrade urgency (e.g., URGENT → ROUTINE)

A violation → immediate fail-closed escalation; the LLM output is discarded.

### Confidence Scoring

Starting score of 1.0, deductions applied deterministically:

| Deduction | Amount | Condition |
|-----------|--------|-----------|
| Missing key info | −0.15 | age OR chief_complaint OR onset_time absent |
| Contradiction | −0.20 | `symptom_severity = "mild"` + red flags present |
| Unclear answer | −0.15 | Caller gave unclear/empty response this turn |
| LLM repair used | −0.20 | Phase1 JSON required a repair call |
| Ambiguous flags below URGENT | −0.30 | Weighted flags present but score < 10 |

If final confidence < **0.60** → escalate to human nurse.

### Confused Caller Protocol

1. **First unclear answer** (empty, "I don't know", filler words, confusion signals): Ask clarification — no LLM call made
2. **Second consecutive unclear answer**: Escalate to human nurse (`fail_reason = "confused_caller_max_retries"`)
3. A clear answer **resets** the retry counter to 0

### JSON Validation with Repair

Phase1 LLM output (`Phase1TurnOutput`) is validated with a 2-attempt loop:

1. **Attempt 1**: Direct parse + Pydantic validation
2. **Attempt 2**: LLM repair call with schema + error message, then re-validate
3. **Both fail** → Fail closed: escalate (`fail_reason = "json_invalid_twice"`)

### Fail-Closed Conditions

The system **always escalates** (never asks another question) when:

| Fail reason | Trigger |
|-------------|---------|
| `red_flag_exception:<exc>` | `score_red_flags()` raises an exception |
| `llm_timeout:<exc>` | Phase1 `_raw_call` raises |
| `json_invalid_twice` | Both validation attempts fail |
| `post_check_violation:<reason>` | Post-check safety gate triggered |
| `low_confidence` | Confidence score < 0.60 after all deductions |
| `confused_caller_max_retries` | Two consecutive unclear answers |

Over-escalation is **always preferred** over under-escalation.

### Decision Trace Logging

Every turn (including escalations) appends a `DecisionTraceEntry` to `session.decision_trace` containing: timestamp, turn number, user text, extracted entities, flags triggered, confidence score, disposition, escalation required, system response, and confidence breakdown. This provides a full auditable record of every clinical decision.

---

## Phase 2: Protocol Grounding (RAG-lite)

Phase 2 adds **clinical protocol retrieval** to supplement the LLM with structured triage guidance. On each turn, relevant protocol snippets are retrieved and injected as context for the LLM, improving clinical grounding while maintaining the Phase 1 safety hierarchy.

### What Phase 2 Adds

- **Protocol Knowledge Base** (`protocols/v1/`): 8 versioned clinical protocols covering common triage categories (chest pain, shortness of breath, abdominal pain, fever, child illness, allergic reaction, neuro/stroke signs, UTI symptoms).
- **RAG-lite Retriever** (`src/protocols/retriever.py`): Deterministic keyword/fuzzy-match retrieval that runs on every turn before the LLM call.
- **Protocol Context Injection**: Retrieved snippets are injected as system messages into both Phase1 and Intake LLM calls.
- **Decision Trace Citations**: `DecisionTraceEntry` now includes `protocol_hits` and `protocol_citations` for audit review.

### Safety Hierarchy (Unchanged)

```
RED FLAGS  >  DETERMINISTIC RULES  >  PROTOCOL  >  LLM
```

Protocol retrieval **NEVER**:
- Overrides red flags or deterministic rules
- Downgrades urgency
- Escalates on retrieval failure (returns empty, continues normally)

### How Protocols Are Updated/Versioned

Protocols live in `protocols/v1/` as individual JSON files. Each file contains:

| Field | Description |
|-------|-------------|
| `id` | Unique identifier (e.g., `PROTO-001`) |
| `title` | Human-readable protocol name |
| `keywords` | List of terms for retrieval matching |
| `body` | Clinical assessment guidance (short, operational) |
| `disposition_notes` | Disposition-level guidance by severity |
| `last_updated` | Date of last revision |
| `version` | Protocol version string |

To add a new protocol: create a new `.json` file in `protocols/v1/` following the same schema. The retriever automatically picks up all `.json` files in the directory. To create a new version set, create a `protocols/v2/` directory and update your retriever configuration.

### How Retrieval Works

1. On each turn, the retriever builds a combined text from: chief complaint (double-weighted), recent utterance, extracted entities, and red flag state.
2. Each protocol is scored using **keyword matching** (exact phrase match, token overlap, and bigram overlap).
3. Protocols scoring above a minimum threshold are returned (top 1–3, ordered by relevance).
4. The orchestrator injects matched protocol excerpts as a system message to the LLM, clearly labelled as supplementary guidance that must not downgrade urgency.
5. Protocol hits are logged in `DecisionTraceEntry.protocol_hits` and `protocol_citations` for nurse/audit review.

### Phase 2 Tests

```powershell
# Run Phase 2 protocol tests (35 cases)
python -m pytest tests/test_protocols.py -v

# Full test suite including Phase 1 + Phase 2 (178 tests)
python -m pytest tests/ --ignore=tests/test_intake_flow.py -v
```

---

## Phase 3: Operational Readiness & Pilot Hardening

Phase 3 adds the infrastructure required for a real pilot deployment: persistent storage, observability, governance, security, and Docker-based deployment.

### What Phase 3 Adds

| Area | Component | Description |
|------|-----------|-------------|
| **Storage** | `src/storage/postgres.py` | PostgreSQL persistence via SQLAlchemy 2.0 |
| **Storage** | `src/storage/models.py` | ORM models for `triage_sessions` and `triage_turns` |
| **Storage** | `src/storage/factory.py` | Backend selection (`memory` / `postgres`) via env var |
| **Migrations** | `alembic/` | Alembic migration framework with initial migration |
| **Observability** | `src/observability/logging.py` | Structured JSON logging with per-request context |
| **Observability** | `src/observability/metrics.py` | Counters, histograms, and `/metrics` endpoint |
| **Governance** | `src/governance/protocol_status.py` | Protocol status gating (draft/approved/deprecated) |
| **Security** | `src/security/middleware.py` | Rate limiting + safe error handling middleware |
| **Config** | `src/config.py` | Centralized env-var configuration with startup validation |
| **Docker** | `Dockerfile`, `docker-compose.yml` | Multi-stage build with Postgres service |

### Safety Hierarchy (Unchanged)

```
RED FLAGS  >  DETERMINISTIC RULES  >  PROTOCOL  >  LLM
```

Phase 3 adds **zero** changes to safety gate logic. Metrics instrumentation is additive only — `metrics.inc()` calls are inserted after existing decision points, never altering them.

### Database Schema

```
triage_sessions                     triage_turns
┌──────────────────────────┐        ┌──────────────────────────┐
│ id  (PK, UUID)           │        │ id  (PK, UUID)           │
│ session_id  (unique)     │        │ session_id  (FK)         │
│ patient_name             │        │ turn_index               │
│ patient_age              │        │ user_text                │
│ patient_sex              │        │ system_text              │
│ chief_complaint          │        │ confidence               │
│ current_phase            │        │ disposition              │
│ disposition              │        │ escalation_required      │
│ escalation_required      │        │ flags_triggered  (JSON)  │
│ confidence               │        │ extracted_entities (JSON) │
│ metadata  (JSON)         │        │ confidence_breakdown     │
│ created_at               │        │ protocol_hits  (JSON)    │
│ updated_at               │        │ created_at               │
└──────────────────────────┘        └──────────────────────────┘
                                    UNIQUE(session_id, turn_index)
```

PHI fields (`user_text`, `system_text`) are controlled by the `STORE_PHI` env var. When `false`, these fields store `"[REDACTED]"`.

### Migrations

```powershell
# Run migrations (requires DATABASE_URL)
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "description"
```

### Governance

Protocol JSON files now include governance fields:

```json
{
  "id": "PROTO-001",
  "status": "approved",
  "effective_date": "2026-01-15",
  "reviewed_by": "Clinical Safety Board",
  "reviewed_at": "2026-01-14T10:00:00",
  "owner": "Emergency Medicine"
}
```

| Environment | Behavior |
|-------------|----------|
| `production` | Only `status: approved` protocols are loaded. Server fails to start if none exist. |
| `development` | All protocols loaded with warnings for non-approved ones. |

### Observability

**Structured Logging:** JSON-formatted logs with contextual fields (session_id, turn_index, disposition, escalation_required) via contextvars.

**Metrics Counters:**
- `triage_sessions_total` — Sessions created
- `triage_escalations_total` — Escalations triggered
- `red_flag_triggers_total` — Red flag detections
- `llm_timeouts_total` — LLM failures
- `json_repairs_total` — JSON repair attempts
- `post_check_violations_total` — Safety gate violations
- `retriever_hits_total` — Protocol retriever matches

**Metrics Histograms:** `confidence_score`, `turn_latency_ms`

**Endpoints:**
- `GET /health` — Liveness probe
- `GET /ready` — Readiness probe (DB connectivity check)
- `GET /metrics` — JSON metrics snapshot

### Security

- **Rate Limiting:** Token-bucket per client IP (configurable via `RATE_LIMIT` env var, default `60/minute`)
- **Safe Error Handling:** Stack traces suppressed in production; detailed errors in development
- **No Secrets in Code:** All sensitive config via environment variables

### Docker Deployment

```powershell
# Build and run with Docker Compose
docker-compose up --build

# Run with custom env
docker-compose --env-file .env up --build
```

Services:
- `api` — FastAPI app on port 8000, depends on postgres
- `postgres` — PostgreSQL 15 with persistent volume

### Environment Variables (Phase 3)

| Variable | Default | Description |
|----------|---------|-------------|
| `STORAGE_BACKEND` | `memory` | `memory` or `postgres` |
| `DATABASE_URL` | — | PostgreSQL connection URL (required when `STORAGE_BACKEND=postgres`) |
| `STORE_PHI` | `false` | Whether to persist user/system text (PHI) |
| `ENVIRONMENT` | `development` | `development` or `production` |
| `PROTOCOL_VERSION` | `v1` | Active protocol version directory |
| `CONFIDENCE_MIN_THRESHOLD` | `0.60` | Min confidence score (0–1) below which system escalates to human nurse |
| `REDFLAG_SCORE_THRESHOLD` | `10` | Weighted integer red-flag score at/above which pre-check escalates to URGENT |
| `RATE_LIMIT` | `60/minute` | API rate limit per client IP |
| `TRUST_PROXY_HEADERS` | `false` | Trust X-Forwarded-For for client IP (enable only behind trusted proxy) |
| `LOG_FORMAT` | `json` | Log format (`json` or `text`) |

### Phase 3 Tests

```powershell
# Phase 3 tests only (54 new cases)
python -m pytest tests/test_phase3_storage.py tests/test_phase3_governance.py tests/test_phase3_observability.py -v

# Full suite (237 tests)
python -m pytest tests/ --ignore=tests/test_intake_flow.py -v
```

---

## CI/CD Pipeline

The project uses GitHub Actions for continuous integration and deployment:

### CI (`.github/workflows/ci.yml`)
Runs on every push to `main` and on all pull requests:
- **Gitleaks**: Secret scanning using [gitleaks/gitleaks-action](https://github.com/gitleaks/gitleaks-action) — **blocks merge** on any detected secret. Custom rules in `.gitleaks.toml` cover DeepSeek keys, Twilio tokens, and standard provider patterns.
- **Lint**: `ruff check` and `ruff format --check` on `src/` and `tests/`
- **Test Suite**: Full `pytest` run with coverage reporting (excludes integration/load tests)
- **Security Scan**: `bandit` static analysis + `safety` dependency vulnerability check

### Auto-Deploy (Azure Container Apps)
The existing Azure deployment workflow triggers on push to `main` after CI passes.

### Running Tests Locally

```bash
# Full test suite (matches CI)
python -m pytest tests/ -v --tb=short

# Golden-call regression tests only
python -m pytest tests/golden_calls/test_golden_calls.py -v

# With coverage
python -m pytest tests/ --cov=src --cov-report=term-missing
```

---

## Monitoring & Error Tracking

Production uses Sentry for error monitoring with full PHI scrubbing. See [`MONITORING.md`](MONITORING.md) for:
- Configuration (set `SENTRY_DSN` to enable)
- PHI safeguard architecture (3 layers of defense)
- Captured events and their data boundaries

---

## Local Dev Setup

> **⚠ WARNING — virtual environments are NOT portable.**
> Never copy or move the `.venv` folder between machines or directories.
> If pytest fails with *"Unable to create process using '...' The system cannot
> find the file specified"*, your `.venv` is stale. Delete it and follow the
> steps below to recreate it in place.

Run the following block in **PowerShell from the repo root** (`C:\...\Nurse-Triage-Assistant`):

```powershell
# 1. Deactivate any active venv (safe to run even if none is active)
if ($env:VIRTUAL_ENV) { deactivate }

# 2. Delete the stale venv
Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue

# 3. Recreate using the system Python (3.11+)
python -m venv .venv

# 4. Activate
.\.venv\Scripts\Activate.ps1

# 5. Install all dependencies
pip install -r requirements.txt

# 6. Smoke-test the environment
python --version
python -m pytest -q --ignore=tests/test_intake_flow.py
```

After this, VS Code will automatically pick up `.venv\Scripts\python.exe` as
the interpreter (configured in `.vscode/settings.json`). If VS Code shows a
different interpreter in the status bar, click it and choose
**"Enter interpreter path…"**, then type:
```
.venv\Scripts\python.exe
```

> `test_intake_flow.py` is an integration test that requires the FastAPI server
> running on port 8001. Skip it for local unit testing with
> `--ignore=tests/test_intake_flow.py`.

---

## Quick Start

### Prerequisites
- Python 3.11+
- DeepSeek API key (set in `.env`)
- Twilio account (for voice calls)

### Setup

```bash
# Clone & install
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env and add your DEEPSEEK_API_KEY
```

### Run the Server

```bash
python run.py
# Server starts at http://127.0.0.1:8000
```

### Run Tests

```powershell
# Unit tests only (no server required)
python -m pytest -q --ignore=tests/test_intake_flow.py

# Phase 1 safety acceptance tests (44 cases)
python -m pytest tests/test_phase1_safety.py -v

# Phase 2 protocol tests (35 cases)
python -m pytest tests/test_protocols.py -v

# Phase 3 operational readiness tests (54 cases)
python -m pytest tests/test_phase3_storage.py tests/test_phase3_governance.py tests/test_phase3_observability.py -v

# Full unit test suite (237 tests)
python -m pytest tests/ --ignore=tests/test_intake_flow.py -v
```

### Simulation Runner

Test the full orchestrator flow offline without Twilio or a live LLM:

```bash
# Run all scenarios with mock LLM
python -m scripts.simulate_calls --mock

# Run a specific scenario
python -m scripts.simulate_calls --mock --scenario chest_pain_emergency

# Run with custom scenarios file
python -m scripts.simulate_calls --mock --file scripts/scenarios.json

# Quiet mode (minimal output)
python -m scripts.simulate_calls --mock --quiet
```

## Project Structure

```
protocols/
  v1/                   # Versioned clinical protocol knowledge base (Phase 2)
    chest_pain.json     # 8 starter protocols: chest pain, SOB, abdominal,
    fever.json          #   fever, child illness, allergic reaction,
    neuro_stroke.json   #   neuro/stroke, UTI symptoms
    ...
src/
  config.py             # Phase 3: Centralized env-var configuration
  orchestrator/         # Multi-agent orchestrator
    orchestrator.py     # Core process_turn() + finalize() logic
    schemas.py          # Pydantic v2 models (session, outputs, flags, protocol hits)
    prompts.py          # System prompts for intake + finalize agents
    validators.py       # JSON extraction + schema validation
  protocols/            # Phase 2: Protocol retrieval (RAG-lite)
    retriever.py        # Keyword/fuzzy-match retriever + protocol loader
  safety/
    red_flags.py        # Deterministic pattern-matching safety rules
  llm/
    client.py           # Structured LLM client (DeepSeek + Pydantic)
    deepseek_client.py  # Legacy DeepSeek client (REST API path)
  storage/
    interface.py        # Abstract StorageInterface
    memory.py           # In-memory implementation with TTL cleanup
    models.py           # Phase 3: SQLAlchemy ORM models
    postgres.py         # Phase 3: PostgreSQL storage backend
    factory.py          # Phase 3: Storage backend factory
  governance/           # Phase 3: Protocol governance controls
    protocol_status.py  # Status gating (draft/approved/deprecated)
  observability/        # Phase 3: Structured logging & metrics
    logging.py          # JSON log formatter + context vars
    metrics.py          # Counters, histograms, /metrics data
  security/             # Phase 3: Security middleware
    middleware.py       # Rate limiting + safe error handling
  twilio/
    routes.py           # Twilio voice webhook handlers
  api/
    routes.py           # REST API intake endpoints
  triage/               # Rule-based triage engine
  shared/               # Shared schemas and state
alembic/                # Phase 3: Database migrations
  versions/
    001_initial.py      # Initial schema migration
tests/
  test_red_flags.py              # Safety rule tests (60+ cases)
  test_validators.py             # JSON validation tests (16 cases)
  test_orchestrator.py           # Integration tests (16 cases)
  test_phase1_safety.py          # Phase 1 acceptance tests (44 cases)
  test_protocols.py              # Phase 2 protocol tests (35 cases)
  test_phase3_storage.py         # Phase 3 storage tests (20 cases)
  test_phase3_governance.py      # Phase 3 governance tests (20 cases)
  test_phase3_observability.py   # Phase 3 observability tests (14 cases)
scripts/
  simulate_calls.py     # Offline simulation runner
  scenarios.json        # Test scenarios
Dockerfile              # Phase 3: Multi-stage Docker build
docker-compose.yml      # Phase 3: API + Postgres services
docs/
  design_notes.md       # Architecture decisions & tradeoffs
```

## Performance Metrics

- **Average Call Duration:** 3-5 minutes for complete intake
- **Accuracy:** High concordance with nurse triage decisions
- **Availability:** 99.9% uptime capability
- **Scalability:** Concurrent call handling with cloud deployment

## Future Roadmap

Planned enhancements:
- **Multilingual Support:** French, Spanish, and other languages for diverse patient populations
- **EHR Integration:** HL7 FHIR compatibility for seamless health record integration
- **Advanced Analytics:** Dashboard with triage metrics, trends, and quality indicators
- **Expanded Protocols:** Comprehensive symptom coverage across medical specialties
- **SMS/Text Option:** Multi-channel intake including text-based alternatives
- **Appointment Scheduling:** Direct booking integration with provider calendars
- **Predictive Analytics:** ML-powered insights for resource planning and capacity management

## Contact & Licensing

This proprietary software is protected by copyright. For licensing inquiries, partnership opportunities, or demonstrations, please contact the development team.

**Note:** This is a commercial healthcare technology product. Unauthorized reproduction or use is prohibited.

## About

Developed by a healthcare innovation team dedicated to improving patient access, reducing wait times, and supporting frontline healthcare workers through intelligent automation.

---

**Disclaimer:** This software is for demonstration and research purposes. It is not FDA-approved and should not be used as a substitute for professional medical advice, diagnosis, or treatment. Always seek the advice of qualified health providers with questions about medical conditions.

**Copyright © 2026. All rights reserved.**
