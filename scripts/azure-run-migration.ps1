# =============================================================================
# Run Alembic Migrations on Azure - Container Apps Job (PowerShell)
#
# Creates (or updates) a one-shot Container Apps Job that runs
# `alembic upgrade head` using the same image as the API.
#
# Usage:
#   .\scripts\azure-run-migration.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

# Load deployment info if available
if (Test-Path ".azure-deploy-info") {
    Get-Content ".azure-deploy-info" | ForEach-Object {
        if ($_ -match '^(\w+)=(.*)$') {
            $varName = $matches[1]
            $varValue = $matches[2]
            # Only set if not already defined via env
            if (-not (Get-Item "env:$varName" -ErrorAction SilentlyContinue)) {
                Set-Item "env:$varName" $varValue
            }
        }
    }
}

$RG = if ($env:RG) { $env:RG }        else { "nurse-triage-rg" }
$APP = if ($env:APP) { $env:APP }       else { "nurse-triage-api" }
$ACR = if ($env:ACR) { $env:ACR }       else { "" }
$ACR_SERVER = if ($env:ACR_SERVER) { $env:ACR_SERVER } else { "" }
$IMAGE_TAG = if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { "5.0.0" }
$JOB_NAME = if ($env:JOB_NAME) { $env:JOB_NAME }  else { "nurse-triage-migrate" }
$ENV_NAME = if ($env:ENV_NAME) { $env:ENV_NAME }  else { "nurse-triage-aca-env" }

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Running Alembic Migrations (Container Apps Job)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Resolve ACR server if not set
if (-not $ACR_SERVER -and $ACR) {
    $ACR_SERVER = az acr show --name $ACR --query loginServer -o tsv
}
if (-not $ACR_SERVER) {
    Write-Host "ERROR: ACR_SERVER not set. Run azure-deploy.ps1 first or set `$env:ACR_SERVER." -ForegroundColor Red
    exit 1
}

# Ensure containerapp extension
az extension add --name containerapp --upgrade --yes 2>$null

# Get ACR credentials
$ACR_USERNAME = az acr credential show --name $ACR --query username -o tsv
$ACR_PASSWORD = az acr credential show --name $ACR --query "passwords[0].value" -o tsv

Write-Host ">>> Creating migration job '$JOB_NAME' ..." -ForegroundColor Green

# Check if job already exists
$JobExists = az containerapp job show `
    --resource-group $RG `
    --name $JOB_NAME `
    --query name -o tsv 2>$null

if ($JobExists) {
    Write-Host "    Job already exists - updating image and re-running ..."
    az containerapp job update `
        --resource-group $RG `
        --name $JOB_NAME `
        --image "${ACR_SERVER}/nurse-triage:${IMAGE_TAG}" `
        --output none
}
else {
    az containerapp job create `
        --resource-group $RG `
        --name $JOB_NAME `
        --environment $ENV_NAME `
        --image "${ACR_SERVER}/nurse-triage:${IMAGE_TAG}" `
        --registry-server $ACR_SERVER `
        --registry-username $ACR_USERNAME `
        --registry-password $ACR_PASSWORD `
        --trigger-type Manual `
        --replica-timeout 300 `
        --replica-retry-limit 1 `
        --cpu 0.5 `
        --memory 1.0Gi `
        --command "python" "-m" "alembic" "upgrade" "head" `
        --output none

    Write-Host "    NOTE: Set DATABASE_URL secret on the job:" -ForegroundColor Yellow
    Write-Host "      az containerapp job secret set -g $RG -n $JOB_NAME --secrets database-url='YOUR_CONN_STRING'"
    Write-Host "      az containerapp job update -g $RG -n $JOB_NAME --set-env-vars DATABASE_URL=secretref:database-url"
}

Write-Host ""
Write-Host ">>> Starting migration job ..." -ForegroundColor Green

$ExecutionName = az containerapp job start `
    --resource-group $RG `
    --name $JOB_NAME `
    --query name -o tsv

Write-Host "    Execution started: $ExecutionName"
Write-Host "    Waiting for completion ..."

# Poll for completion (max 5 minutes)
$MaxWait = 300
$Elapsed = 0
$Interval = 10

while ($Elapsed -lt $MaxWait) {
    $Status = az containerapp job execution show `
        --resource-group $RG `
        --name $JOB_NAME `
        --job-execution-name $ExecutionName `
        --query "properties.status" -o tsv 2>$null

    if (-not $Status) { $Status = "Running" }

    if ($Status -eq "Succeeded") {
        Write-Host ""
        Write-Host "    Migration completed successfully!" -ForegroundColor Green
        Write-Host ""
        Write-Host "    View logs:"
        Write-Host "      az containerapp job logs show -g $RG -n $JOB_NAME --execution $ExecutionName"
        exit 0
    }
    elseif ($Status -eq "Failed") {
        Write-Host ""
        Write-Host "    ERROR: Migration job failed!" -ForegroundColor Red
        Write-Host "    View logs:"
        Write-Host "      az containerapp job logs show -g $RG -n $JOB_NAME --execution $ExecutionName"
        exit 1
    }

    Start-Sleep -Seconds $Interval
    $Elapsed += $Interval
    Write-Host "    ... status: $Status (${Elapsed}s elapsed)"
}

Write-Host ""
Write-Host "    WARNING: Timed out waiting for migration job (${MaxWait}s)." -ForegroundColor Yellow
Write-Host "    Check status manually:"
Write-Host "      az containerapp job execution show -g $RG -n $JOB_NAME --job-execution-name $ExecutionName"
exit 2
