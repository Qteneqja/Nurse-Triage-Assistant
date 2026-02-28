# Azure Deployment Guide — Nurse Triage Assistant v5.0.0

Production deployment to **Azure Container Apps** with PostgreSQL Flexible Server and Azure Container Registry.

---

## Architecture

```
Twilio ──HTTPS──► Azure Container Apps (nurse-triage-api)
                      │
                      ├── ACR image: nurse-triage:5.0.0
                      ├── Ingress: external (public HTTPS FQDN)
                      └── Env vars + Secrets
                              │
                              ▼
                  Azure PostgreSQL Flexible Server
                      (TLS/SSL required)
```

## Prerequisites

| Tool | Install |
|------|---------|
| Azure CLI | `winget install Microsoft.AzureCLI` or [aka.ms/install-azure-cli](https://aka.ms/install-azure-cli) |
| Azure subscription | With permissions to create resources |

Scripts are provided in both **PowerShell** (`.ps1`, for Windows) and **Bash** (`.sh`, for WSL/Linux/macOS).

**Secrets you'll need:**
- `DEEPSEEK_API_KEY` — your DeepSeek API key
- `TWILIO_AUTH_TOKEN` — from the Twilio Console
- A strong password for PostgreSQL admin

---

## Quick Start

### PowerShell (Windows)

```powershell
# 1. Install Azure CLI (if not already)
winget install Microsoft.AzureCLI
# Restart your terminal after install

# 2. Login to Azure
az login

# 3. Set required secrets
$env:PG_ADMIN_PASS = "YourStrongPassword123!"
$env:DEEPSEEK_API_KEY = "sk-..."
$env:TWILIO_AUTH_TOKEN = "your-twilio-auth-token"

# 4. Deploy everything
.\scripts\azure-deploy.ps1

# 5. Run database migrations
.\scripts\azure-run-migration.ps1

# 6. Verify deployment
.\scripts\azure-verify.ps1
```

### Bash (WSL / Linux / macOS)

```bash
# 1. Login to Azure
az login

# 2. Set required secrets
export PG_ADMIN_PASS='YourStrongPassword123!'
export DEEPSEEK_API_KEY='sk-...'
export TWILIO_AUTH_TOKEN='your-twilio-auth-token'

# 3. Deploy everything
./scripts/azure-deploy.sh

# 4. Run database migrations
./scripts/azure-run-migration.sh

# 5. Verify deployment
./scripts/azure-verify.sh
```

---

## Step-by-Step Walkthrough

### Step 1 — Create Azure Resources

The deployment script creates:

| Resource | Purpose |
|----------|---------|
| Resource Group (`nurse-triage-rg`) | Logical container for all resources |
| Container Registry (ACR) | Private Docker image registry |
| PostgreSQL Flexible Server | Production database with TLS |
| Container Apps Environment | Hosting environment for the API |
| Container App | The running API (public HTTPS ingress) |

**Customize** resource names via environment variables:

```bash
export RG=my-custom-rg
export LOC=eastus
export IMAGE_TAG=5.0.1
./scripts/azure-deploy.sh
```

### Step 2 — Database Migrations

Migrations run as a **Container Apps Job** (one-shot task), not on app startup. This is the recommended Azure-native pattern for production.

```bash
./scripts/azure-run-migration.sh
```

The script:
1. Creates a manual-trigger Container Apps Job using the same image
2. Executes `alembic upgrade head`
3. Waits for completion and reports status

**Run this on every release** before shifting traffic to the new version.

### Step 3 — Configure Twilio

After deployment, the script prints your public FQDN. Set it in Twilio:

1. Go to **Twilio Console** → **Phone Numbers** → your number
2. Under **Voice & Fax** → **A Call Comes In**:
   - **Webhook**: `https://<FQDN>/api/v1/voice/incoming`
   - **HTTP POST**
3. Save

The app validates Twilio request signatures (`TWILIO_VALIDATE_SIGNATURE=true`) so only genuine Twilio requests are accepted.

### Step 4 — Verify

```bash
./scripts/azure-verify.sh
```

Checks:
- `GET /health` → 200 (liveness probe)
- `GET /ready` → 200 (DB connectivity)
- `GET /metrics` → 200
- API version = 5.0.0
- X-Request-ID header present
- TLS certificate valid
- Voice webhook route exists

---

## Updating Secrets

Update API keys or tokens without redeploying:

```bash
export DEEPSEEK_API_KEY='new-key'
./scripts/azure-update-secrets.sh
```

The container restarts automatically to pick up new values.

---

## Environment Variables Reference

| Variable | Production Default | Source |
|----------|-------------------|--------|
| `APP_ENV` | `production` | env var |
| `STORAGE_BACKEND` | `postgres` | env var |
| `DATABASE_URL` | — | secret → `secretref:database-url` |
| `DEEPSEEK_API_KEY` | — | secret → `secretref:deepseek-api-key` |
| `TWILIO_AUTH_TOKEN` | — | secret → `secretref:twilio-auth-token` |
| `TWILIO_VALIDATE_SIGNATURE` | `true` | env var |
| `TWILIO_WEBHOOK_BASE_URL` | `https://<FQDN>` | env var (set by deploy) |
| `LOG_FORMAT` | `json` | env var |
| `TRUST_PROXY_HEADERS` | `true` | env var |
| `PROTOCOL_VERSION` | `v1` | env var |
| `CONFIDENCE_MIN_THRESHOLD` | `0.60` | env var |
| `REDFLAG_SCORE_THRESHOLD` | `10` | env var |
| `RATE_LIMIT` | `60/minute` | env var |
| `RUN_MIGRATIONS_ON_STARTUP` | `false` | env var |
| `CORS_ALLOWED_ORIGINS` | (your domain) | env var |

---

## Scaling

Azure Container Apps auto-scales by default. Configured range: **1–3 replicas**.

Adjust:
```bash
az containerapp update -g nurse-triage-rg -n nurse-triage-api \
  --min-replicas 2 --max-replicas 10
```

---

## Custom Domain (Optional)

```bash
# Add custom hostname
az containerapp hostname add \
  -g nurse-triage-rg -n nurse-triage-api \
  --hostname api.yourdomain.com

# Bind a managed certificate
az containerapp hostname bind \
  -g nurse-triage-rg -n nurse-triage-api \
  --hostname api.yourdomain.com \
  --environment nurse-triage-aca-env \
  --validation-method CNAME
```

Then update your DNS CNAME to point to the ACA FQDN, and update `TWILIO_WEBHOOK_BASE_URL` + `CORS_ALLOWED_ORIGINS`:

```bash
export TWILIO_WEBHOOK_BASE_URL=https://api.yourdomain.com
export CORS_ALLOWED_ORIGINS=https://dashboard.yourdomain.com
./scripts/azure-update-secrets.sh
```

---

## Redeploying a New Version

```bash
# Build new image
az acr build -r <ACR_NAME> -t nurse-triage:5.1.0 .

# Update the container app
az containerapp update -g nurse-triage-rg -n nurse-triage-api \
  --image <ACR_SERVER>/nurse-triage:5.1.0

# Run migrations first
IMAGE_TAG=5.1.0 ./scripts/azure-run-migration.sh

# Verify
./scripts/azure-verify.sh
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `/ready` returns 503 | Check PostgreSQL firewall rules — ACA needs access |
| Twilio calls fail | Verify `TWILIO_AUTH_TOKEN` matches Console; check `TWILIO_WEBHOOK_BASE_URL` |
| Config validation error at startup | Ensure all required env vars are set (see table above) |
| Migration job fails | Check logs: `az containerapp job logs show -g nurse-triage-rg -n nurse-triage-migrate` |
| TLS errors | ACA provides managed TLS; ensure custom domain CNAME is correct |

View container logs:
```bash
az containerapp logs show -g nurse-triage-rg -n nurse-triage-api --follow
```

---

## Cost Estimate (Approximate)

| Resource | SKU | ~Monthly |
|----------|-----|----------|
| Container Apps | 1 vCPU / 2 GiB, 1 replica | ~$36 |
| PostgreSQL Flexible | B1ms (1 vCPU, 2 GiB) | ~$25 |
| Container Registry | Basic | ~$5 |
| **Total** | | **~$66/month** |

Scale resources up as needed for production traffic.
