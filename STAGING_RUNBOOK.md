# Staging Runbook — Azure Container Apps
<!-- Purpose: Operational reference for staging environment validation -->
<!-- Date Created: 2026-03-02 -->
<!-- Task: 4 — Staging Runbook -->

## Environment Overview

| Component | Service | Status |
|-----------|---------|--------|
| Compute | Azure Container Apps | ✅ Deployed |
| Database | Azure Managed Postgres | ✅ Provisioned |
| Telephony | Twilio Voice | Configured |
| Monitoring | Sentry | DSN configured |
| LLM | DeepSeek API | Key configured |

---

## 1. Required Environment Variables

These must be set in the Azure Container App configuration.
**Do NOT store values in this document.**

| Variable | Required | Example Format | Notes |
|----------|----------|---------------|-------|
| `APP_ENV` | Yes | `staging` | Enables staging-mode validation |
| `ENVIRONMENT` | Yes | `staging` | Backward-compat alias for APP_ENV |
| `DATABASE_URL` | Yes | `postgresql://...` | Managed Postgres connection string |
| `STORAGE_BACKEND` | Yes | `postgres` | Must be `postgres` in staging |
| `DEEPSEEK_API_KEY` | Yes | `sk-...` | LLM API key |
| `DEEPSEEK_BASE_URL` | No | `https://api.deepseek.com` | Default is fine |
| `DEEPSEEK_MODEL` | No | `deepseek-chat` | Default is fine |
| `TWILIO_AUTH_TOKEN` | Yes | (32-char hex) | For webhook signature validation |
| `TWILIO_ACCOUNT_SID` | Yes | `AC...` | Twilio account identifier |
| `TWILIO_VALIDATE_SIGNATURE` | Auto | `true` | Auto-enabled in staging |
| `TWILIO_WEBHOOK_BASE_URL` | If proxied | `https://staging.example.com` | Set if behind reverse proxy |
| `SENTRY_DSN` | Recommended | `https://...@sentry.io/...` | Staging Sentry project |
| `STORE_PHI` | No | `false` | Default false — set true only if needed |
| `NURSE_TRANSFER_NUMBER` | No | `+18005551234` | Nurse queue number for warm transfer |
| `AZURE_STORAGE_CONNECTION_STRING` | If blob | (conn string) | For report blob storage |
| `AZURE_BLOB_CONTAINER` | No | `triage-reports` | Default container name |
| `HOST` | No | `0.0.0.0` | Default is fine |
| `PORT` | No | `8000` | Match container port config |
| `RUN_MIGRATIONS_ON_STARTUP` | Optional | `true` | Run Alembic migrations on boot |

> **TODO (fill from Azure Portal):** Confirm the exact Container App name, resource group, and public URL.

---

## 2. Health Checks

### Liveness Probe: `GET /health`

```bash
curl -s https://<STAGING_URL>/health
```

**Expected response (HTTP 200):**
```json
{"status": "ok", "timestamp": "2026-03-02T12:00:00.000Z"}
```

**If this fails:** The container process is down or not accepting connections. Check container logs.

### Readiness Probe: `GET /ready`

```bash
curl -s https://<STAGING_URL>/ready
```

**Expected response (HTTP 200):**
```json
{"status": "ready", "database": "connected"}
```

**Expected failure response (HTTP 503):**
```json
{"status": "not_ready", "database": "unavailable"}
```

> Note: In production/staging, the error detail is intentionally omitted to avoid leaking internals.

**If this fails:** Database connection issue. Check:
- `DATABASE_URL` is correct
- Postgres server is running and accepting connections
- Firewall rules allow Container App → Postgres
- SSL mode is compatible (`?sslmode=require` for Azure Managed Postgres)

---

## 3. Twilio Verification Checklist

### Prerequisites
- [ ] Staging Twilio phone number configured
- [ ] Webhook URL points to staging Container App
- [ ] `TWILIO_AUTH_TOKEN` set in Container App env vars
- [ ] `TWILIO_VALIDATE_SIGNATURE` is `true` (auto in staging)

### Validation Steps

| # | Test | How | Expected Result |
|---|------|-----|-----------------|
| 1 | Valid Twilio call | Call the staging Twilio number | Call is accepted, intake begins |
| 2 | Invalid signature | `curl -X POST https://<STAGING_URL>/voice/incoming -d "test=1"` | HTTP 403 `{"error": "Invalid Twilio signature"}` |
| 3 | Missing signature header | Same curl without X-Twilio-Signature | HTTP 403 |
| 4 | Check security log | Review container logs after test 2 | Security warning logged with IP and endpoint |

> **Note:** Twilio signature validation cannot be reliably tested with plain `curl` for valid signatures — the signature depends on the exact URL, POST body, and auth token. Use the Twilio CLI, console, or an actual phone call for valid-signature testing.

> **TODO (fill from Azure Portal):** Staging Twilio number, staging webhook URL.

---

## 4. Observability Checklist

### Sentry Validation

| # | Check | How | Expected |
|---|-------|-----|----------|
| 1 | Sentry receives events | Trigger an error (e.g., invalid API call) | Event appears in Sentry staging project |
| 2 | `session_id` tag present | Open event in Sentry | Tag visible in event context |
| 3 | `call_sid` tag present | Open event from Twilio call | Tag visible |
| 4 | No PHI in events | Review event data in Sentry | No patient names, transcripts, symptoms in any field |
| 5 | Request body scrubbed | Check event request tab | Body shows `[REDACTED — PHI SAFEGUARD]` |

**Safe way to trigger a test Sentry event (no PHI):**
- Set `DEEPSEEK_API_KEY` to an invalid value temporarily
- Make a call → LLM timeout/auth failure → Sentry captures the exception
- Verify the event in Sentry, then restore the correct key

### Container Logs

```bash
# View recent logs (Azure CLI)
az containerapp logs show \
  --name <APP_NAME> \
  --resource-group <RESOURCE_GROUP> \
  --tail 100
```

> **TODO (fill from Azure Portal):** Container App name, resource group.

---

## 5. Database Checklist

After a successful test call through staging:

| # | Check | SQL Query | Expected |
|---|-------|-----------|----------|
| 1 | Session created | `SELECT * FROM sessions ORDER BY created_at DESC LIMIT 1;` | Row exists with session_id matching the call |
| 2 | Decision trace stored | `SELECT * FROM decision_traces WHERE session_id = '<id>';` | Row with disposition, confidence, rules_triggered |
| 3 | SBAR stored | Check blob storage or SBAR table (depends on config) | SBAR document with S/B/A/R sections |
| 4 | Audit entry | `SELECT * FROM audit_log WHERE session_id = '<id>';` | Audit trail entry |

> **TODO:** Confirm exact table names from Alembic migration files. Run `alembic current` to verify migration state.

**Connect to staging Postgres:**
```bash
# Via Azure CLI
az postgres flexible-server connect \
  --name <POSTGRES_SERVER> \
  --admin-user <USER> \
  --database triage
```

---

## 6. Staging Smoke Test — Quick Commands

```bash
# 1. Health check
curl -sf https://<STAGING_URL>/health && echo "PASS: /health" || echo "FAIL: /health"

# 2. Readiness check
curl -sf https://<STAGING_URL>/ready && echo "PASS: /ready" || echo "FAIL: /ready"

# 3. Invalid signature rejection (expects 403)
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST https://<STAGING_URL>/voice/incoming -d "test=1")
[ "$HTTP_CODE" = "403" ] && echo "PASS: signature rejection ($HTTP_CODE)" || echo "FAIL: expected 403, got $HTTP_CODE"

# 4. API docs (if enabled)
curl -sf https://<STAGING_URL>/docs && echo "PASS: /docs accessible" || echo "INFO: /docs not available (may be disabled in staging)"
```

> **TODO (fill from Azure Portal):** Replace `<STAGING_URL>` with actual staging URL.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `/health` returns 404 | Health endpoint not registered | Check `src/main.py` or `src/api/` for route registration |
| `/ready` returns 503 | Database connection failed | Check `DATABASE_URL`, Postgres status, firewall rules |
| Twilio calls get 403 | Signature validation failing | Check `TWILIO_AUTH_TOKEN` matches Twilio dashboard, check `TWILIO_WEBHOOK_BASE_URL` if behind proxy |
| Sentry receives no events | `SENTRY_DSN` not set or empty | Verify env var in Container App config |
| LLM timeout errors | DeepSeek API unreachable or key invalid | Verify `DEEPSEEK_API_KEY`, check DeepSeek status page |
| Container won't start | Config validation failure | Check container logs — `require_valid_config()` will log specific missing vars |
