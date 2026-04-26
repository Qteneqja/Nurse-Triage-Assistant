# GPT-5.5 Project Onboarding Prompt

---

You are being brought in as a senior software architect and AI systems engineer to help plan and execute the next stage of an AI-powered medical triage platform. Before I share my roadmap, here is a complete briefing on what has been built and where the system stands today.

---

## 1. What This System Is

**Nurse Triage Assistant v5.0.0** — an AI-powered automated telephone triage system designed for hospitals and nurse triage lines (e.g., Health Links in Winnipeg, Canada). It receives patient phone calls via Twilio Voice, conducts a structured medical intake conversation, applies deterministic safety rules, makes a triage recommendation, and generates SBAR-formatted clinical handoff summaries for nurse review.

**It is not a diagnostic tool.** It triages urgency only. Every output is reviewed by a human clinician. The system is intentionally designed to over-triage (escalate up) rather than under-triage.

---

## 2. Current Deployment State

The system is **live in production on Azure Container Apps** as of April 26, 2026.

| Component | Details |
|-----------|---------|
| **Cloud** | Microsoft Azure — Canada Central |
| **Runtime** | Azure Container Apps (`nurse-triage-api`) |
| **App URL** | `https://nurse-triage-api.livelymushroom-186460d5.canadacentral.azurecontainerapps.io` |
| **Health** | `GET /health → 200 {"status":"ok"}` — confirmed live |
| **Container Image** | `nursetriageacr7351.azurecr.io/nurse-triage:5.0.0-linux` (linux/amd64) |
| **Database** | Azure PostgreSQL Flexible Server — `nurse-triage-pg-7351.postgres.database.azure.com` |
| **DB Migrations** | Both Alembic migrations run: `001_initial` + `002_phase4_hardening` |
| **Twilio Webhook** | Configured: `+14314502019 → POST /api/v1/voice/incoming` |
| **LLM** | DeepSeek (`deepseek-chat`) via `https://api.deepseek.com` |
| **Observability** | Sentry integration, structured JSON logging, per-request correlation IDs |
| **Scale** | min 1 replica, max 3, 0.5 vCPU / 1Gi RAM |

---

## 3. Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend Framework** | Python 3.12, FastAPI |
| **LLM** | DeepSeek API (deepseek-chat model) |
| **Voice/Telephony** | Twilio Voice API + TwiML webhooks |
| **Storage** | PostgreSQL (Azure Flexible Server) via SQLAlchemy + Alembic |
| **Schemas/Validation** | Pydantic v2 — every LLM output validated before use |
| **Containerisation** | Docker (single-stage, linux/amd64) |
| **CI** | GitHub Actions |
| **Monitoring** | Sentry, structured logging (JSON), PHI masking filter on all log output |
| **Security middleware** | Rate limiting, CORS, Twilio signature validation, trust-proxy-headers |

---

## 4. Codebase Architecture

```
src/
├── main.py                    # FastAPI app — lifespan, middleware, routers
├── config.py                  # All env-var config, typed, validated at startup
├── api/
│   ├── routes.py              # REST intake API (programmatic / testing)
│   └── reports.py             # Session reports endpoint
├── twilio/
│   └── routes.py              # Twilio Voice webhook handlers (TwiML responses)
├── orchestrator/
│   ├── orchestrator.py        # Multi-agent orchestration loop (core logic)
│   ├── schemas.py             # All Pydantic schemas for LLM I/O
│   ├── prompts.py             # System prompts (intake turn, finalize, phase1)
│   ├── validators.py          # Post-check safety gate, repair loops
│   └── intake_gate.py        # Transfer-of-control gate logic
├── safety/
│   ├── red_flags.py           # Deterministic red-flag detection (check_all, score_red_flags)
│   ├── red_flag_rules.py      # Regex-based rule definitions
│   ├── gate.py                # Final safety gate: wraps every triage output
│   ├── phi_masking.py         # PHI scrubber — applied to all log output
│   ├── safety_gate.py         # Per-turn safety validation
│   ├── diagnosis_enforcement.py  # Prevents LLM from making diagnoses
│   └── triage_output_schema.py   # Output schema enforcement
├── llm/
│   ├── client.py              # DeepSeek LLM client, JSON repair, retry
│   └── guarded_client.py      # GuardedLLM wrapper — safety-enforced LLM calls
├── storage/
│   ├── interface.py           # StorageInterface ABC
│   ├── postgres.py            # PostgreSQL backend
│   ├── memory.py              # In-memory backend (dev/test only)
│   ├── models.py              # SQLAlchemy ORM models
│   ├── session_repository.py  # Repository pattern for session persistence
│   └── factory.py             # Backend factory, enforces postgres in production
├── governance/
│   └── protocol_status.py     # Validates approved clinical protocols exist at startup
├── protocols/
│   └── retriever.py           # Protocol snippet retrieval for clinical prompts
├── observability/
│   ├── logging.py             # Structured logging, correlation IDs, context vars
│   ├── metrics.py             # In-process metrics collection
│   └── sentry_integration.py  # Sentry setup, breadcrumbs, PHI-safe error capture
├── security/
│   └── middleware.py          # Security headers, rate limiting, CORS
└── shared/                    # Shared utilities
```

---

## 5. Core Data Flow

```
Incoming phone call (Twilio)
  │
  └─► POST /api/v1/voice/incoming (Twilio webhook, signature validated)
        │
        ├─ Scripted stages: NAME → AGE → SEX → CHIEF_COMPLAINT (deterministic TwiML)
        │
        └─ DYNAMIC stage (Orchestrator):
              │
              ├─ 1. Deterministic Red-Flag Check (regex rules, score_red_flags())
              │       CRITICAL flag → ER_NOW  (LLM bypassed entirely)
              │       score ≥ 10    → URGENT  (LLM bypassed entirely)
              │
              ├─ 2. Protocol Retrieval (version-controlled clinical protocols)
              │
              ├─ 3. Single DeepSeek LLM call per turn
              │       Output validated against Phase1TurnOutput Pydantic schema
              │       JSON repair attempted on failure (2 retries)
              │
              ├─ 4. Post-check Safety Gate (prevents diagnoses, unsafe instructions)
              │
              ├─ 5. State Update — check stop conditions:
              │       confidence ≥ 0.75, max 12 turns, all required fields collected
              │
              └─ 6. Finalization (1 LLM call)
                      DispositionCategory + SBAR + safety-net instructions
                      → Stored to PostgreSQL
                      → TwiML response returned to caller
```

---

## 6. Disposition Taxonomy

| Value | Meaning | Urgency |
|-------|---------|---------|
| `ER_NOW` | Immediate emergency — call 911 | Immediate |
| `URGENT` | Urgent care or ER within hours | < 4 hours |
| `SCHEDULE` | Schedule with primary care | < 24–48 hours |
| `SELF_CARE` | Low acuity, self-care instructions | Informational |
| `HUMAN_REVIEW` | Insufficient info / uncertainty — nurse callback | < 30 min |

---

## 7. Safety Architecture Principles

- **Deterministic rules always run first, always override LLM.** The red-flag engine uses regex pattern matching and cannot be bypassed.
- **Every LLM output is Pydantic-validated** before any downstream use. Invalid JSON triggers a repair loop (up to 2 retries), then defaults to `HUMAN_REVIEW`.
- **PHI masking on all log output.** Patient names, phone numbers, DOBs are scrubbed before any log line is written or sent to Sentry.
- **Twilio webhook signature validation** — all incoming voice requests are authenticated.
- **Production enforces Postgres** — in-memory storage raises `RuntimeError` if `ENVIRONMENT=production`.
- **Design bias:** Over-triage (escalate up) on any uncertainty.

---

## 8. Database Schema (2 Applied Migrations)

### `001_initial` — Core Session Tables
- `triage_sessions` — one row per call (session_id, call_sid, patient demographics, disposition, confidence, SBAR)
- `triage_turns` — one row per conversation turn (transcript, LLM input/output, red flags triggered)

### `002_phase4_hardening` — Audit & Safety
- `audit_log` — immutable audit trail of all system actions
- `safety_events` — records every safety gate trigger
- `rule_triggers` — records every deterministic rule that fired
- PHI masking columns enforced

---

## 9. Clinical Protocols

- Protocol version: `v1` (pinned via `PROTOCOL_VERSION` env var)
- Located in `protocols/v1/`
- Governance module validates approved protocols exist at startup
- Protocol snippets are injected into LLM prompts at runtime

---

## 10. Tests

Full test suite in `tests/` covering:
- Phase 1 safety (red flags, escalation logic)
- Phase 3 governance, observability, storage
- Phase 4 hardening (audit log, safety events)
- Phase 5 infrastructure (middleware, security)
- Intake flow end-to-end
- LLM client (mock responses, validation, repair)
- Twilio webhook authentication
- Canonical disposition enforcement
- No-bypass guarantee (safety rules cannot be overridden)

---

## 11. Pilot Readiness Status

The system is entering a **2–4 week clinical pilot** with real patient calls under nurse supervision.

### Pilot Rules
- ALL dispositions are reviewed by a nurse during the pilot (no autonomous final decisions)
- Every `ER_NOW` → caller told to call 911, nurse notified immediately
- Every `URGENT` → nurse callback within 15 minutes
- Every `HUMAN_REVIEW` → nurse callback within 30 minutes

### Primary Safety Gates (pilot stops if breached)
- Under-triage rate < 5%
- Critical miss rate = 0% (ER_NOW-worthy call given lower disposition → stop pilot immediately)
- Red-flag detection rate = 100%
- System availability > 99%

### Known Open Items
- Gitleaks not yet in CI pipeline
- GitHub Secret Scanning not yet enabled
- Git history contains one test/placeholder key (`sk-12345678901234567890`) from an early commit — assessed as non-blocking but rewrite is recommended before public repo sharing
- Credential rotation recommended (ACR, PostgreSQL, DeepSeek, Twilio — all were used in deployment session)

---

## 12. What the System Does NOT Do (Important Context)

1. Does not diagnose — it triages urgency only
2. Does not prescribe medications
3. Does not make autonomous final clinical decisions
4. Does not handle non-English callers
5. Does not fine-tune or learn from patient data
6. Does not store unencrypted PHI

---

## Your Task

I have been building a plan for the next stage of this project. I am about to share it with you. Please:

1. Read the plan carefully and ask any clarifying questions before suggesting changes
2. Identify any technical, clinical safety, or architectural conflicts with the current system as described above
3. Propose a phased implementation approach that builds incrementally on the existing foundation without breaking the current safety architecture
4. Flag any items that require clinical review or governance approval before implementation
5. Be explicit about what changes touch the safety-critical path (red-flag engine, safety gate, disposition logic) — those require the highest scrutiny

Ready for the plan.
