#!/usr/bin/env bash
# =============================================================================
# Update Secrets — Nurse Triage Assistant (Azure Container Apps)
#
# Update API keys, tokens, or DB connection strings without redeploying.
#
# Usage:
#   export DEEPSEEK_API_KEY="new-key"
#   ./scripts/azure-update-secrets.sh
#
# Only non-empty values are updated. Omit a variable to leave it unchanged.
# =============================================================================

set -euo pipefail

if [[ -f .azure-deploy-info ]]; then
    # shellcheck source=/dev/null
    source .azure-deploy-info
fi

RG="${RG:-nurse-triage-rg}"
APP="${APP:-nurse-triage-api}"

echo "============================================================"
echo " Update Azure Container App Secrets"
echo "============================================================"
echo " Resource Group: $RG"
echo " App           : $APP"
echo ""

SECRETS_ARGS=""
ENV_ARGS=""

# DeepSeek API Key
if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
    SECRETS_ARGS="${SECRETS_ARGS} deepseek-api-key=${DEEPSEEK_API_KEY}"
    echo "  [UPDATE] deepseek-api-key"
fi

# Twilio Auth Token
if [[ -n "${TWILIO_AUTH_TOKEN:-}" ]]; then
    SECRETS_ARGS="${SECRETS_ARGS} twilio-auth-token=${TWILIO_AUTH_TOKEN}"
    echo "  [UPDATE] twilio-auth-token"
fi

# Database URL
if [[ -n "${DATABASE_URL:-}" ]]; then
    SECRETS_ARGS="${SECRETS_ARGS} database-url=${DATABASE_URL}"
    echo "  [UPDATE] database-url"
fi

if [[ -z "$SECRETS_ARGS" ]]; then
    echo "  No secrets to update. Set env vars before running:"
    echo "    export DEEPSEEK_API_KEY='new-key'"
    echo "    export TWILIO_AUTH_TOKEN='new-token'"
    echo "    export DATABASE_URL='postgresql://...'"
    exit 0
fi

echo ""
echo ">>> Updating secrets ..."
az containerapp secret set \
    --resource-group "$RG" \
    --name "$APP" \
    --secrets $SECRETS_ARGS \
    --output none

echo "    Secrets updated."

# Update env vars if CORS or other non-secret values are provided
if [[ -n "${CORS_ALLOWED_ORIGINS:-}" ]]; then
    echo ">>> Updating CORS_ALLOWED_ORIGINS ..."
    az containerapp update \
        --resource-group "$RG" \
        --name "$APP" \
        --set-env-vars "CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS}" \
        --output none
    echo "    CORS updated."
fi

if [[ -n "${TWILIO_WEBHOOK_BASE_URL:-}" ]]; then
    echo ">>> Updating TWILIO_WEBHOOK_BASE_URL ..."
    az containerapp update \
        --resource-group "$RG" \
        --name "$APP" \
        --set-env-vars "TWILIO_WEBHOOK_BASE_URL=${TWILIO_WEBHOOK_BASE_URL}" \
        --output none
    echo "    Twilio webhook URL updated."
fi

echo ""
echo "Done. The container will restart automatically to pick up new secrets."
echo "============================================================"
