#!/usr/bin/env bash
# =============================================================================
# Update Secrets — Nurse Triage Assistant (Azure Container Apps)
#
# Update API keys, tokens, or DB connection strings without redeploying.
#
# Usage:
#   export DEEPSEEK_API_KEY="<replacement-key>"
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

SECRETS_ARGS=()
SECRET_ENV_ARGS=()
NON_SECRET_ENV_ARGS=()

# DeepSeek API Key
if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
    SECRETS_ARGS+=("deepseek-api-key=${DEEPSEEK_API_KEY}")
    SECRET_ENV_ARGS+=("DEEPSEEK_API_KEY=secretref:deepseek-api-key")
    echo "  [UPDATE] deepseek-api-key"
fi

# Twilio Auth Token
if [[ -n "${TWILIO_AUTH_TOKEN:-}" ]]; then
    SECRETS_ARGS+=("twilio-auth-token=${TWILIO_AUTH_TOKEN}")
    SECRET_ENV_ARGS+=("TWILIO_AUTH_TOKEN=secretref:twilio-auth-token")
    echo "  [UPDATE] twilio-auth-token"
fi

# Database URL
if [[ -n "${DATABASE_URL:-}" ]]; then
    SECRETS_ARGS+=("database-url=${DATABASE_URL}")
    SECRET_ENV_ARGS+=("DATABASE_URL=secretref:database-url")
    echo "  [UPDATE] database-url"
fi

# Non-secret env vars
if [[ -n "${CORS_ALLOWED_ORIGINS:-}" ]]; then
    NON_SECRET_ENV_ARGS+=("CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS}")
    echo "  [UPDATE] CORS_ALLOWED_ORIGINS"
fi

if [[ -n "${TWILIO_WEBHOOK_BASE_URL:-}" ]]; then
    NON_SECRET_ENV_ARGS+=("TWILIO_WEBHOOK_BASE_URL=${TWILIO_WEBHOOK_BASE_URL}")
    echo "  [UPDATE] TWILIO_WEBHOOK_BASE_URL"
fi

if [[ -n "${ENABLE_SHARED_NUMBER_VERTICAL_MENU:-}" ]]; then
    NON_SECRET_ENV_ARGS+=(
        "ENABLE_SHARED_NUMBER_VERTICAL_MENU=${ENABLE_SHARED_NUMBER_VERTICAL_MENU}"
    )
    echo "  [UPDATE] ENABLE_SHARED_NUMBER_VERTICAL_MENU"
fi

if [[ -n "${SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER:-}" ]]; then
    NON_SECRET_ENV_ARGS+=(
        "SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER=${SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER}"
    )
    echo "  [UPDATE] SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER"
fi

if [[ ${#SECRETS_ARGS[@]} -eq 0 && ${#NON_SECRET_ENV_ARGS[@]} -eq 0 ]]; then
    echo "  No secrets to update. Set env vars before running:"
    echo "    export DEEPSEEK_API_KEY='<replacement-key>'"
    echo "    export TWILIO_AUTH_TOKEN='<replacement-token>'"
    echo "    export DATABASE_URL='<postgres-connection-string>'"
    echo "    export ENABLE_SHARED_NUMBER_VERTICAL_MENU='true'"
    exit 0
fi

if [[ ${#SECRETS_ARGS[@]} -gt 0 ]]; then
    echo ""
    echo ">>> Updating secrets ..."
    az containerapp secret set \
        --resource-group "$RG" \
        --name "$APP" \
        --secrets "${SECRETS_ARGS[@]}" \
        --output none

    echo "    Secrets updated."

    echo ">>> Pointing app env vars at secret references ..."
    az containerapp update \
        --resource-group "$RG" \
        --name "$APP" \
        --set-env-vars "${SECRET_ENV_ARGS[@]}" \
        --output none
    echo "    Env vars now reference Container App secrets."
fi

# Update non-secret env vars if provided
if [[ ${#NON_SECRET_ENV_ARGS[@]} -gt 0 ]]; then
    echo ">>> Updating non-secret env vars ..."
    az containerapp update \
        --resource-group "$RG" \
        --name "$APP" \
        --set-env-vars "${NON_SECRET_ENV_ARGS[@]}" \
        --output none
    echo "    Non-secret env vars updated."
fi

echo ""
echo "Done. The container will restart automatically to pick up new secrets."
echo "============================================================"
