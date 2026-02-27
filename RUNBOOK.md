# RUNBOOK — Nurse Triage Assistant

> Phase 5 — SaaS Infrastructure & Deployment

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [How to Run Locally](#how-to-run-locally)
3. [How to Deploy (Production)](#how-to-deploy-production)
4. [Database Migrations](#database-migrations)
5. [Environment Variables](#environment-variables)
6. [Verification Checklist](#verification-checklist)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Tool | Minimum Version |
|---|---|
| Python | 3.12+ |
| Docker | 24+ |
| Docker Compose | v2+ |
| PostgreSQL | 16 (via Docker) |

---

## How to Run Locally

### Option A: Native (no Docker)

```bash
# 1. Create virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy environment file
cp .env.example .env
# Edit .env — at minimum set DEEPSEEK_API_KEY

# 4. Run the server (dev mode with reload)
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload

# 5. Verify
curl http://localhost:8000/health
# → {"status": "healthy"}
```

### Option B: Docker Compose (recommended)

```bash
# 1. Copy env file
cp .env.example .env
# Edit .env — set DEEPSEEK_API_KEY, POSTGRES_PASSWORD

# 2. Build and start
docker compose up --build -d

# 3. Run migrations (first time or after schema changes)
docker compose run --rm --profile tools migrate

# 4. Verify
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

---

## How to Deploy (Production)

### Pre-deploy checklist

- [ ] `DEEPSEEK_API_KEY` set as platform secret
- [ ] `TWILIO_AUTH_TOKEN` set as platform secret
- [ ] `POSTGRES_PASSWORD` set to a strong random value
- [ ] `DATABASE_URL` points to production Postgres
- [ ] `TWILIO_WEBHOOK_BASE_URL` set to the public URL Twilio will call
- [ ] `CORS_ALLOWED_ORIGINS` set to allowed frontend domains (comma-separated)

### Docker Compose (production overlay)

```bash
# Set env vars on the host or use a platform secrets manager.
# Do NOT use a .env file in production.

export APP_ENV=production
export DEEPSEEK_API_KEY=sk-...
export TWILIO_AUTH_TOKEN=...
export POSTGRES_PASSWORD=$(openssl rand -base64 32)
export DATABASE_URL=postgresql://triage:${POSTGRES_PASSWORD}@postgres:5432/triage_db
export TWILIO_WEBHOOK_BASE_URL=https://api.yourdomain.com

# Deploy with production overlay
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d

# Or run migrations separately first:
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm --profile tools migrate
```

### Gunicorn (standalone, no Docker)

```bash
gunicorn src.main:app \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --workers 4 \
  --timeout 120 \
  --graceful-timeout 30 \
  --access-logfile -
```

### Key production defaults

| Setting | Value | Reason |
|---|---|---|
| `APP_ENV` | `production` | Strict config validation |
| `STORAGE_BACKEND` | `postgres` | Required in prod |
| `TWILIO_VALIDATE_SIGNATURE` | `true` (auto) | Prevents forged webhook calls |
| `CORS_ALLOWED_ORIGINS` | explicit list | No wildcard in prod |
| `LOG_FORMAT` | `json` | Structured for log aggregation |
| `TRUST_PROXY_HEADERS` | `true` | When behind ALB/nginx |
| `RUN_MIGRATIONS_ON_STARTUP` | `true` or manual | Either auto or pre-deploy step |

---

## Database Migrations

### Run migrations manually (recommended for production deployments)

```bash
# Via Docker Compose
docker compose run --rm --profile tools migrate

# Via local Alembic
DATABASE_URL=postgresql://triage:pass@localhost:5432/triage_db \
  python -m alembic upgrade head
```

### Auto-run on startup

Set `RUN_MIGRATIONS_ON_STARTUP=true`. Alembic `upgrade head` is idempotent — safe to run repeatedly. In production, the startup will **fail hard** if a migration fails.

### Create a new migration

```bash
python -m alembic revision --autogenerate -m "description_of_change"
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `APP_ENV` | No | `development` | Environment profile: `development`, `staging`, `production` |
| `STORAGE_BACKEND` | No | `memory` | `memory` (dev) or `postgres` (staging/prod) |
| `DATABASE_URL` | When postgres | — | PostgreSQL connection string |
| `DEEPSEEK_API_KEY` | staging/prod | — | LLM API key |
| `DEEPSEEK_BASE_URL` | No | `https://api.deepseek.com` | LLM endpoint |
| `DEEPSEEK_MODEL` | No | `deepseek-chat` | LLM model name |
| `TWILIO_AUTH_TOKEN` | When sig validation on | — | Twilio account auth token |
| `TWILIO_WEBHOOK_BASE_URL` | For sig validation | — | Public URL Twilio calls |
| `TWILIO_VALIDATE_SIGNATURE` | No | `false` (dev) / `true` (staging/prod) | Verify X-Twilio-Signature |
| `NURSE_TRANSFER_NUMBER` | No | — | Phone number for warm transfer |
| `USE_MOCK_LLM` | No | `false` | Skip real LLM calls (testing) |
| `STORE_PHI` | No | `false` | Store raw PHI in DB/reports |
| `RATE_LIMIT` | No | `60/minute` | Rate limit per client IP |
| `TRUST_PROXY_HEADERS` | No | `false` | Trust X-Forwarded-For |
| `CORS_ALLOWED_ORIGINS` | No | `*` (dev) / empty (prod) | Comma-separated origins |
| `LOG_FORMAT` | No | `json` | `json` or `text` |
| `RUN_MIGRATIONS_ON_STARTUP` | No | `false` | Auto-run Alembic on boot |
| `POSTGRES_PASSWORD` | Docker Compose | `triage_pass` | Postgres password |
| `PROTOCOL_VERSION` | No | `v1` | Clinical protocol version |
| `LLM_TIMEOUT` | No | `30` | LLM call timeout (seconds) |
| `CONFIDENCE_MIN_THRESHOLD` | No | `0.60` | Escalation confidence floor |
| `REDFLAG_SCORE_THRESHOLD` | No | `10` | Red-flag score threshold |

---

## Verification Checklist

Run these commands after deployment to confirm everything works:

```bash
# 1. Docker build
docker build -t triage-api .

# 2. Docker compose up
docker compose up -d
sleep 15  # wait for healthchecks

# 3. Liveness
curl -s http://localhost:8000/health
# Expected: {"status":"healthy"}

# 4. Readiness (DB connected)
curl -s http://localhost:8000/ready
# Expected: {"status":"ready","storage":"postgres","database":"connected"}

# 5. Run migrations
docker compose run --rm --profile tools migrate
# Expected: "INFO  [alembic.runtime.migration] Running upgrade ..."

# 6. Test webhook endpoint (dev mode, no sig validation)
curl -s -X POST http://localhost:8000/api/v1/voice/incoming \
  -d "CallSid=CA1234567890abcdef&From=%2B15551234567" \
  -H "Content-Type: application/x-www-form-urlencoded"
# Expected: XML TwiML response with <Gather> and greeting

# 7. Correlation ID header returned
curl -sv http://localhost:8000/health 2>&1 | grep -i x-request-id
# Expected: < x-request-id: <uuid>

# 8. Run tests
python -m pytest tests/ -x -q

# 9. Check structured logging
docker compose logs api --tail=20
# Expected: JSON lines with "request_id", "level", "message" fields
```

---

## Troubleshooting

### "Configuration validation failed" on startup

The app validates all required env vars for the selected `APP_ENV`. Check the error message for which var is missing. Common issues:
- `APP_ENV=production` but `DEEPSEEK_API_KEY` not set
- `STORAGE_BACKEND=postgres` but `DATABASE_URL` missing
- `TWILIO_VALIDATE_SIGNATURE=true` but `TWILIO_AUTH_TOKEN` missing

### Database connection refused

```bash
# Check Postgres is running
docker compose ps postgres
# Check connectivity
docker compose exec postgres pg_isready -U triage -d triage_db
# Check DATABASE_URL matches compose config
echo $DATABASE_URL
```

### Twilio 403 Forbidden on webhooks

Signature validation is enabled. Verify:
1. `TWILIO_AUTH_TOKEN` matches your Twilio account
2. `TWILIO_WEBHOOK_BASE_URL` matches the URL configured in Twilio console
3. Set `TWILIO_VALIDATE_SIGNATURE=false` in dev to disable

### Rate limiting (429 Too Many Requests)

Default: 60 requests/minute per IP. Adjust via `RATE_LIMIT` env var (e.g. `120/minute`).
Health/ready/metrics endpoints are exempt from rate limiting.

### PHI in logs

PHI masking is **always on** via the `PHIMaskingFilter` on the root logger. To store unmasked PHI in reports, set `STORE_PHI=true` (not recommended for production).
