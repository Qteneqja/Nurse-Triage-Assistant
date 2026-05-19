#!/usr/bin/env bash
# =============================================================================
# Run Alembic Migrations on Azure — via Container Apps Job
#
# This script creates (or updates) a one-shot Container Apps Job that
# executes `alembic upgrade head` using the same image as the API.
#
# Usage:
#   ./scripts/azure-run-migration.sh
#
# The .azure-deploy-info file (created by azure-deploy.sh) is sourced
# automatically. Override any value via environment variables.
# =============================================================================

set -euo pipefail

# Load deployment info if available
if [[ -f .azure-deploy-info ]]; then
    # shellcheck source=/dev/null
    source .azure-deploy-info
fi

# Allow overrides
RG="${RG:-nurse-triage-rg}"
APP="${APP:-nurse-triage-api}"
ACR="${ACR:-}"
ACR_SERVER="${ACR_SERVER:-}"
IMAGE_TAG="${IMAGE_TAG:-5.0.0}"
JOB_NAME="${JOB_NAME:-nurse-triage-migrate}"
ENV_NAME="${ENV_NAME:-nurse-triage-aca-env}"

echo "============================================================"
echo " Running Alembic Migrations (Container Apps Job)"
echo "============================================================"
echo ""

# Resolve ACR server if not set
if [[ -z "$ACR_SERVER" && -n "$ACR" ]]; then
    ACR_SERVER=$(az acr show --name "$ACR" --query loginServer -o tsv)
fi

if [[ -z "$ACR_SERVER" ]]; then
    echo "ERROR: ACR_SERVER not set. Run azure-deploy.sh first or set ACR_SERVER env var."
    exit 1
fi

# Ensure containerapp extension
az extension add --name containerapp --upgrade --yes 2>/dev/null || true

# Get ACR credentials
ACR_USERNAME=$(az acr credential show --name "$ACR" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR" --query "passwords[0].value" -o tsv)

# Get DATABASE_URL secret from the running app
echo ">>> Creating migration job '$JOB_NAME' ..."

# Check if job already exists
JOB_EXISTS=$(az containerapp job show \
    --resource-group "$RG" \
    --name "$JOB_NAME" \
    --query name -o tsv 2>/dev/null || echo "")

if [[ -n "$JOB_EXISTS" ]]; then
    echo "    Job already exists — updating image and re-running ..."
    az containerapp job update \
        --resource-group "$RG" \
        --name "$JOB_NAME" \
        --image "$ACR_SERVER/nurse-triage:$IMAGE_TAG" \
        --output none
else
    # Create the job (manual trigger, runs once per invocation)
    az containerapp job create \
        --resource-group "$RG" \
        --name "$JOB_NAME" \
        --environment "$ENV_NAME" \
        --image "$ACR_SERVER/nurse-triage:$IMAGE_TAG" \
        --registry-server "$ACR_SERVER" \
        --registry-username "$ACR_USERNAME" \
        --registry-password "$ACR_PASSWORD" \
        --trigger-type Manual \
        --replica-timeout 300 \
        --replica-retry-limit 1 \
        --cpu 0.5 \
        --memory 1.0Gi \
        --command "python" "-m" "alembic" "upgrade" "head" \
        --output none

    # Copy secrets from the main app to the job
    # We need DATABASE_URL for Alembic to connect
    echo "    Setting migration job secrets ..."

    # Pull DATABASE_URL from app's secrets — re-use the same secret
    az containerapp job secret set \
        --resource-group "$RG" \
        --name "$JOB_NAME" \
        --secrets database-url=secretref:database-url \
        --output none 2>/dev/null || true

    # Since we can't directly copy secretrefs between apps, we need
    # the actual DATABASE_URL. If you stored it, pass it as env var.
    # Otherwise, we set env vars referencing secrets.
    echo "    NOTE: You may need to set the DATABASE_URL secret on the job manually:"
    echo "      az containerapp job secret set -g $RG -n $JOB_NAME --secrets database-url='<postgres-connection-string>'"
    echo "      az containerapp job update -g $RG -n $JOB_NAME --set-env-vars DATABASE_URL=secretref:database-url"
fi

echo ""
echo ">>> Starting migration job ..."

EXECUTION_NAME=$(az containerapp job start \
    --resource-group "$RG" \
    --name "$JOB_NAME" \
    --query name -o tsv)

echo "    Execution started: $EXECUTION_NAME"
echo "    Waiting for completion ..."

# Poll for completion (max 5 minutes)
MAX_WAIT=300
ELAPSED=0
INTERVAL=10

while [[ $ELAPSED -lt $MAX_WAIT ]]; do
    STATUS=$(az containerapp job execution show \
        --resource-group "$RG" \
        --name "$JOB_NAME" \
        --job-execution-name "$EXECUTION_NAME" \
        --query "properties.status" -o tsv 2>/dev/null || echo "Running")

    if [[ "$STATUS" == "Succeeded" ]]; then
        echo ""
        echo "    Migration completed successfully!"
        echo ""
        echo "    View logs:"
        echo "      az containerapp job logs show -g $RG -n $JOB_NAME --execution $EXECUTION_NAME"
        exit 0
    elif [[ "$STATUS" == "Failed" ]]; then
        echo ""
        echo "    ERROR: Migration job failed!"
        echo "    View logs for details:"
        echo "      az containerapp job logs show -g $RG -n $JOB_NAME --execution $EXECUTION_NAME"
        exit 1
    fi

    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
    echo "    ... status: $STATUS (${ELAPSED}s elapsed)"
done

echo ""
echo "    WARNING: Timed out waiting for migration job (${MAX_WAIT}s)."
echo "    Check status manually:"
echo "      az containerapp job execution show -g $RG -n $JOB_NAME --job-execution-name $EXECUTION_NAME"
exit 2
