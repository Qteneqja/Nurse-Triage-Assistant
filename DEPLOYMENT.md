# Deployment Guide

**Date:** 2026-03-02  
**Step:** Production Hardening — Step 6

## Prerequisites

- Docker 24+ with Compose V2
- Azure Container Apps (production target)
- PostgreSQL 16+ (Azure Database for PostgreSQL or container)
- Environment variables configured (see below)

## Environment Variables

### Required in Production

| Variable | Description |
|----------|-------------|
| `APP_ENV` | Must be `production` |
| `DEEPSEEK_API_KEY` | DeepSeek LLM API key |
| `DATABASE_URL` | PostgreSQL connection string |
| `TWILIO_AUTH_TOKEN` | Twilio auth token for webhook signature validation |
| `TWILIO_WEBHOOK_BASE_URL` | Public URL for Twilio webhooks |
| `POSTGRES_PASSWORD` | PostgreSQL password (docker-compose) |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `SENTRY_DSN` | _(empty)_ | Sentry DSN — enables error monitoring when set |
| `PROTOCOL_VERSION` | `v1` | Clinical protocol version |
| `CONFIDENCE_MIN_THRESHOLD` | `0.60` | Minimum confidence for triage output |
| `REDFLAG_SCORE_THRESHOLD` | `10` | Score threshold for red-flag escalation |
| `LOG_FORMAT` | `json` | Log format (`json` or `text`) |
| `CORS_ALLOWED_ORIGINS` | _(empty)_ | Comma-separated CORS origins |
| `RUN_MIGRATIONS_ON_STARTUP` | `true` (prod) | Auto-run Alembic migrations at startup |
| `BIRCHWOOD_COLLISION_WORKFLOW_ID` | `birchwood_collision_intake_v1` | Which collision workflow the Birchwood number uses. Default = the live pilot flow (minimal pure-intake) the Aurora voice pass targets; set to `birchwood_collision_intake_min_v1` for the separate minimal-only package. A DB `phone_numbers` route, if present, takes precedence over this config route. |

### Voice pipeline (telephony transport)

| Variable | Default | Description |
|----------|---------|-------------|
| `VOICE_PIPELINE` | `gather` | `gather` (legacy `<Gather>`/TwiML) or `conversation_relay` (streaming WebSocket). Rollback = set back to `gather`. Keep `gather` until the staging latency gate passes (ADR 0002). |
| `VOICE_OUTPUT_MODE` | `azure_play` | CR output: `azure_play` (keep Azure voice via `play` URL, no streaming) or `cr_native` (stream tokens, CR renders TTS — changes the voice). |
| `CONVERSATION_RELAY_WSS_URL` | _(derived)_ | Explicit `wss://` URL for `<ConversationRelay>`; derived from `TWILIO_WEBHOOK_BASE_URL` + `/api/v1/voice/relay` when empty. |
| `CONVERSATION_RELAY_WS_TOKEN` | _(empty)_ | Shared secret appended to the wss URL (`?token=`) to authenticate the WS upgrade. Set in staging/production. |
| `CR_TTS_PROVIDER` | `ElevenLabs` | CR-native TTS provider (`ElevenLabs`/`Google`/`Amazon`). |
| `CR_TTS_VOICE` | _(empty)_ | CR-native TTS voice id. |
| `CR_TRANSCRIPTION_PROVIDER` | `Deepgram` | CR STT provider (`Deepgram`/`Google`). |
| `CR_SPEECH_MODEL` | _(empty)_ | CR STT model (e.g. `nova-3-general`). |

To enable ConversationRelay on staging: set `VOICE_PIPELINE=conversation_relay`,
`TWILIO_WEBHOOK_BASE_URL` (or `CONVERSATION_RELAY_WSS_URL`), and `CONVERSATION_RELAY_WS_TOKEN`;
keep `VOICE_OUTPUT_MODE=azure_play` for no voice change. Then run the latency gate
(`python -m scripts.measure_voice_latency` + the staged-call methodology it prints) before
making `conversation_relay` the default.

## Local Development (Docker Compose)

```bash
# Start all services (API + PostgreSQL)
docker compose up -d

# Run database migrations
docker compose run --rm migrate

# View logs
docker compose logs -f api

# Stop all services
docker compose down
```

## Production Deployment (Docker Compose)

```bash
# Start with production overlay
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Verify health
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

## Azure Container Apps

The repository includes an auto-deploy workflow (`.github/workflows/nurse-triage-api-AutoDeployTrigger-*.yml`) that builds and deploys on push to `main`.

### Manual Deployment

See [docs/AZURE_DEPLOYMENT.md](docs/AZURE_DEPLOYMENT.md) for:
- Azure CLI deployment scripts
- Secret configuration via `scripts/azure-update-secrets.ps1`
- Migration execution via `scripts/azure-run-migration.ps1`
- Verification via `scripts/azure-verify.ps1`

## Docker Image Details

- **Base image**: `python:3.12-slim` (multi-stage build)
- **Port**: 8000
- **User**: Non-root `appuser`
- **Health check**: `curl -f http://localhost:8000/health` every 30s
- **Workers**: 1 (single process, in-memory sessions) — increase after enabling Postgres session storage

## Health Checks

| Endpoint | Purpose | Success Response |
|----------|---------|-----------------|
| `GET /health` | Liveness — is the process running? | `{"status": "ok", "timestamp": "..."}` |
| `GET /ready` | Readiness — is the app connected to backing services? | `{"status": "ready", "database": "connected"}` |

The Docker health check targets `/health`. Kubernetes/Azure readiness probes should target `/ready`.

## Security Notes

- The container runs as non-root (`appuser`)
- `.dockerignore` excludes tests, secrets, `.env` files, and development artifacts
- Production enforces `TWILIO_VALIDATE_SIGNATURE=true` automatically
- Sentry PHI scrubbing is active by default when `SENTRY_DSN` is set
- See [`SECURITY_CLEANUP.md`](SECURITY_CLEANUP.md) for the security audit report

## Rollback

To rollback to a previous version:

```bash
# Azure Container Apps
az containerapp revision list --name nurse-triage-api --resource-group nurse-triage-eastus-rg
az containerapp revision activate --name <revision-name> --resource-group nurse-triage-eastus-rg

# Docker Compose
docker compose pull  # if using a registry
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```
