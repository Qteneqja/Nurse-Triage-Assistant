# Monitoring & Error Tracking

**Date:** 2026-03-02  
**Step:** Production Hardening — Step 4

## Overview

This application uses [Sentry](https://sentry.io/) for error monitoring in production. The integration is designed with **HIPAA-compliant PHI safeguards** — three layers of defense ensure no patient data reaches Sentry.

## Configuration

| Environment Variable | Required | Description |
|---------------------|----------|-------------|
| `SENTRY_DSN` | No | Sentry project DSN. When empty, Sentry is disabled (zero overhead). |
| `APP_ENV` | No | Environment tag sent to Sentry (`development`, `staging`, `production`). |

### Enabling Sentry

Set the `SENTRY_DSN` environment variable to your project's DSN:

```bash
export SENTRY_DSN="https://<key>@<org>.ingest.sentry.io/<project_id>"
```

When `SENTRY_DSN` is not set or empty, Sentry is completely disabled with zero runtime overhead.

## PHI Safeguards (Defense in Depth)

### Layer 1: SDK Configuration
- `send_default_pii=False` — Sentry SDK will not auto-collect user data, cookies, or form bodies.

### Layer 2: `before_send` Hook (`_scrub_phi`)
Every event passes through the PHI scrubbing hook before leaving the process:
- **Request body** → `[REDACTED — PHI SAFEGUARD]`
- **Cookies** → `[REDACTED]`
- **Headers** → Only allowlisted headers pass through: `content-type`, `user-agent`, `x-request-id`, `x-forwarded-for`
- **Breadcrumb data** → Keys containing PHI-risk words (`transcript`, `symptom`, `patient`, `caller`, `message`, `body`, `text`, `content`, `input`) are redacted

### Layer 3: Capture Point Discipline
All explicit capture points (see below) are designed to NEVER include:
- Transcript content
- Patient names, DOB, phone numbers, or addresses
- Symptom descriptions or clinical notes
- Any caller-provided free-text data

## Capture Points

The following events are reported to Sentry:

| Event | Severity | Module | Data Captured |
|-------|----------|--------|---------------|
| LLM API failure/timeout | `error` | `src/llm/client.py` | Model name, timeout duration, retry count, error type |
| JSON validation failure | `warning` | `src/llm/client.py` | Schema name, validation error message |
| Safety gate override | breadcrumb | `src/orchestrator/orchestrator.py` | Rule name, disposition override |
| DB connection failure | `error` | `src/storage/factory.py` | Error type name only |

## Context Tags

The following tags are attached to Sentry events for filtering:

| Tag | Source | PHI Risk |
|-----|--------|----------|
| `session_id` | Opaque UUID | None |
| `call_sid` | Twilio CallSid | None |
| `environment` | `APP_ENV` config | None |

**NEVER tagged:** Patient name, DOB, phone, transcript, symptoms, address.

## Architecture

```
FastAPI startup (lifespan)
    └── init_sentry()
            ├── Reads SENTRY_DSN env var
            ├── sentry_sdk.init(send_default_pii=False, before_send=_scrub_phi)
            └── Returns True/False

Exception Paths:
    src/llm/client.py       → capture_llm_failure()
    src/llm/client.py       → capture_json_validation_failure()
    src/storage/factory.py   → capture_db_failure()
    src/orchestrator/...     → add_safety_gate_breadcrumb()
```

## Testing

Run Step 4 tests:

```bash
python -m pytest tests/test_step4_sentry.py -v
```

Tests verify:
1. Sentry does NOT initialize without `SENTRY_DSN`
2. Sentry initializes correctly with `SENTRY_DSN`
3. Request body is scrubbed from events
4. Only allowlisted headers survive
5. Non-request event structures are preserved
6. Breadcrumb PHI-risk data is redacted
7. Capture functions are safe no-ops when Sentry is unavailable
