#!/usr/bin/env bash
# =============================================================================
# Azure Deployment Script — Nurse Triage Assistant v5.0.0
#
# Creates all Azure resources and deploys the API to Container Apps.
#
# Prerequisites:
#   - Azure CLI installed and logged in (`az login`)
#   - Bash shell (WSL, Git Bash, Cloud Shell, or Linux/macOS)
#
# Usage:
#   # 1. Edit the CONFIGURATION section below
#   # 2. Run:
#   chmod +x scripts/azure-deploy.sh
#   ./scripts/azure-deploy.sh
#
# Idempotent: safe to re-run (existing resources are updated, not duplicated).
# =============================================================================

set -euo pipefail

# =============================================================================
# CONFIGURATION — Edit these before running
# =============================================================================

RG="${RG:-nurse-triage-rg}"
LOC="${LOC:-canadacentral}"
ACR="${ACR:-nursetriageacr${RANDOM}}"
PG="${PG:-nurse-triage-pg-${RANDOM}}"
APP="${APP:-nurse-triage-api}"
ENV_NAME="${ENV_NAME:-nurse-triage-aca-env}"

# PostgreSQL credentials (change these!)
PG_ADMIN_USER="${PG_ADMIN_USER:-triageadmin}"
PG_ADMIN_PASS="${PG_ADMIN_PASS:-}"     # REQUIRED — set via env or change here
PG_DB_NAME="${PG_DB_NAME:-triage_db}"

# Application secrets (REQUIRED — set via env vars before running)
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
TWILIO_AUTH_TOKEN="${TWILIO_AUTH_TOKEN:-}"

# Image tag
IMAGE_TAG="${IMAGE_TAG:-5.0.0}"

# Optional: custom domain for CORS
DASHBOARD_DOMAIN="${DASHBOARD_DOMAIN:-}"

# =============================================================================
# PRE-FLIGHT CHECKS
# =============================================================================

echo "============================================================"
echo " Nurse Triage Assistant — Azure Deployment"
echo "============================================================"
echo ""

# Check Azure CLI
if ! command -v az &> /dev/null; then
    echo "ERROR: Azure CLI (az) is not installed. Install from https://aka.ms/install-azure-cli"
    exit 1
fi

# Check login
if ! az account show &> /dev/null 2>&1; then
    echo "ERROR: Not logged in to Azure. Run 'az login' first."
    exit 1
fi

# Validate required secrets
if [[ -z "$PG_ADMIN_PASS" ]]; then
    echo "ERROR: PG_ADMIN_PASS is not set."
    echo "  export PG_ADMIN_PASS='YourStrongPassword123!'"
    exit 1
fi
if [[ -z "$DEEPSEEK_API_KEY" ]]; then
    echo "ERROR: DEEPSEEK_API_KEY is not set."
    echo "  export DEEPSEEK_API_KEY='your-key'"
    exit 1
fi
if [[ -z "$TWILIO_AUTH_TOKEN" ]]; then
    echo "WARNING: TWILIO_AUTH_TOKEN is not set. Twilio signature validation will fail."
    echo "  Set it now or update later with: scripts/azure-update-secrets.sh"
    echo ""
fi

SUBSCRIPTION=$(az account show --query name -o tsv)
echo "Subscription : $SUBSCRIPTION"
echo "Resource Group: $RG"
echo "Location      : $LOC"
echo "ACR           : $ACR"
echo "PostgreSQL    : $PG"
echo "Container App : $APP"
echo "Environment   : $ENV_NAME"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# =============================================================================
# STEP 1: Resource Group
# =============================================================================

echo ""
echo ">>> Step 1/6: Creating Resource Group '$RG' in '$LOC' ..."
az group create \
    --name "$RG" \
    --location "$LOC" \
    --output none

echo "    Resource Group ready."

# =============================================================================
# STEP 2: Azure Container Registry (ACR)
# =============================================================================

echo ""
echo ">>> Step 2/6: Creating Container Registry '$ACR' ..."
az acr create \
    --resource-group "$RG" \
    --name "$ACR" \
    --sku Basic \
    --admin-enabled true \
    --output none

ACR_SERVER=$(az acr show --name "$ACR" --query loginServer -o tsv)
echo "    ACR login server: $ACR_SERVER"

# Build the Docker image directly in ACR (no local Docker needed)
echo "    Building image $ACR_SERVER/nurse-triage:$IMAGE_TAG in ACR ..."
az acr build \
    --registry "$ACR" \
    --image "nurse-triage:$IMAGE_TAG" \
    . \
    --no-logs

echo "    Image built and pushed: $ACR_SERVER/nurse-triage:$IMAGE_TAG"

# =============================================================================
# STEP 3: PostgreSQL Flexible Server
# =============================================================================

echo ""
echo ">>> Step 3/6: Creating PostgreSQL Flexible Server '$PG' ..."
az postgres flexible-server create \
    --resource-group "$RG" \
    --name "$PG" \
    --location "$LOC" \
    --admin-user "$PG_ADMIN_USER" \
    --admin-password "$PG_ADMIN_PASS" \
    --database-name "$PG_DB_NAME" \
    --sku-name Standard_B1ms \
    --tier Burstable \
    --storage-size 32 \
    --version 16 \
    --public-access 0.0.0.0 \
    --yes \
    --output none

PG_FQDN=$(az postgres flexible-server show \
    --resource-group "$RG" \
    --name "$PG" \
    --query fullyQualifiedDomainName -o tsv)

# Connection string with SSL (Azure requires sslmode=require at minimum)
DATABASE_URL="postgresql://${PG_ADMIN_USER}:${PG_ADMIN_PASS}@${PG_FQDN}:5432/${PG_DB_NAME}?sslmode=require"

echo "    PostgreSQL FQDN : $PG_FQDN"
echo "    Database         : $PG_DB_NAME"
echo "    Connection string: (stored securely — not printed)"

# =============================================================================
# STEP 4: Container Apps Environment
# =============================================================================

echo ""
echo ">>> Step 4/6: Creating Container Apps Environment '$ENV_NAME' ..."

# Ensure the containerapp extension is installed
az extension add --name containerapp --upgrade --yes 2>/dev/null || true

az containerapp env create \
    --resource-group "$RG" \
    --name "$ENV_NAME" \
    --location "$LOC" \
    --output none

echo "    Container Apps Environment ready."

# =============================================================================
# STEP 5: Deploy Container App
# =============================================================================

echo ""
echo ">>> Step 5/6: Creating Container App '$APP' ..."

# Get ACR credentials for the container app
ACR_USERNAME=$(az acr credential show --name "$ACR" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR" --query "passwords[0].value" -o tsv)

# Build CORS origin string
CORS_ORIGINS=""
if [[ -n "$DASHBOARD_DOMAIN" ]]; then
    CORS_ORIGINS="https://${DASHBOARD_DOMAIN}"
fi

az containerapp create \
    --resource-group "$RG" \
    --name "$APP" \
    --environment "$ENV_NAME" \
    --image "$ACR_SERVER/nurse-triage:$IMAGE_TAG" \
    --ingress external \
    --target-port 8000 \
    --min-replicas 1 \
    --max-replicas 3 \
    --cpu 1.0 \
    --memory 2.0Gi \
    --registry-server "$ACR_SERVER" \
    --registry-username "$ACR_USERNAME" \
    --registry-password "$ACR_PASSWORD" \
    --output none

echo "    Container App created."

# =============================================================================
# STEP 6: Set secrets + environment variables
# =============================================================================

echo ""
echo ">>> Step 6/6: Configuring secrets and environment variables ..."

# Set secrets (sensitive values)
SECRETS="deepseek-api-key=${DEEPSEEK_API_KEY}"
SECRETS="${SECRETS} database-url=${DATABASE_URL}"
if [[ -n "$TWILIO_AUTH_TOKEN" ]]; then
    SECRETS="${SECRETS} twilio-auth-token=${TWILIO_AUTH_TOKEN}"
fi

az containerapp secret set \
    --resource-group "$RG" \
    --name "$APP" \
    --secrets $SECRETS \
    --output none

# Get the FQDN for Twilio webhook config
APP_FQDN=$(az containerapp show \
    --resource-group "$RG" \
    --name "$APP" \
    --query properties.configuration.ingress.fqdn -o tsv)

# Set environment variables (reference secrets where needed)
ENV_VARS="APP_ENV=production"
ENV_VARS="${ENV_VARS} ENVIRONMENT=production"
ENV_VARS="${ENV_VARS} STORAGE_BACKEND=postgres"
ENV_VARS="${ENV_VARS} LOG_FORMAT=json"
ENV_VARS="${ENV_VARS} TRUST_PROXY_HEADERS=true"
ENV_VARS="${ENV_VARS} TWILIO_VALIDATE_SIGNATURE=true"
ENV_VARS="${ENV_VARS} PROTOCOL_VERSION=v1"
ENV_VARS="${ENV_VARS} CONFIDENCE_MIN_THRESHOLD=0.60"
ENV_VARS="${ENV_VARS} REDFLAG_SCORE_THRESHOLD=10"
ENV_VARS="${ENV_VARS} RATE_LIMIT=60/minute"
ENV_VARS="${ENV_VARS} RUN_MIGRATIONS_ON_STARTUP=false"
ENV_VARS="${ENV_VARS} DEEPSEEK_API_KEY=secretref:deepseek-api-key"
ENV_VARS="${ENV_VARS} DATABASE_URL=secretref:database-url"
ENV_VARS="${ENV_VARS} TWILIO_WEBHOOK_BASE_URL=https://${APP_FQDN}"
if [[ -n "$TWILIO_AUTH_TOKEN" ]]; then
    ENV_VARS="${ENV_VARS} TWILIO_AUTH_TOKEN=secretref:twilio-auth-token"
fi
if [[ -n "$CORS_ORIGINS" ]]; then
    ENV_VARS="${ENV_VARS} CORS_ALLOWED_ORIGINS=${CORS_ORIGINS}"
fi

az containerapp update \
    --resource-group "$RG" \
    --name "$APP" \
    --set-env-vars $ENV_VARS \
    --output none

echo "    Secrets and env vars configured."

# =============================================================================
# SUMMARY
# =============================================================================

echo ""
echo "============================================================"
echo " DEPLOYMENT COMPLETE"
echo "============================================================"
echo ""
echo " App URL        : https://${APP_FQDN}"
echo " Health check   : https://${APP_FQDN}/health"
echo " Readiness check: https://${APP_FQDN}/ready"
echo ""
echo " Twilio Voice Webhook:"
echo "   POST https://${APP_FQDN}/api/v1/voice/incoming"
echo ""
echo " NEXT STEPS:"
echo "   1. Run migrations:"
echo "      ./scripts/azure-run-migration.sh"
echo ""
echo "   2. Verify deployment:"
echo "      ./scripts/azure-verify.sh"
echo ""
echo "   3. Configure Twilio webhook:"
echo "      In Twilio Console → Phone Number → Voice & Fax"
echo "      Set 'A CALL COMES IN' webhook to:"
echo "        POST https://${APP_FQDN}/api/v1/voice/incoming"
echo ""
echo "   4. (Optional) Add custom domain:"
echo "      az containerapp hostname add \\"
echo "        -g $RG -n $APP --hostname api.yourdomain.com"
echo ""

# Save deployment info for other scripts
cat > .azure-deploy-info <<EOF
RG=$RG
LOC=$LOC
ACR=$ACR
ACR_SERVER=$ACR_SERVER
PG=$PG
PG_FQDN=$PG_FQDN
PG_DB_NAME=$PG_DB_NAME
APP=$APP
ENV_NAME=$ENV_NAME
APP_FQDN=$APP_FQDN
IMAGE_TAG=$IMAGE_TAG
EOF

echo " Deployment info saved to .azure-deploy-info"
echo "============================================================"
