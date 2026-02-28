# =============================================================================
# Update Secrets - Nurse Triage Assistant (PowerShell)
#
# Update API keys, tokens, or DB connection strings without redeploying.
#
# Usage:
#   $env:DEEPSEEK_API_KEY = "new-key"
#   .\scripts\azure-update-secrets.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

# Load deployment info if available
if (Test-Path ".azure-deploy-info") {
    Get-Content ".azure-deploy-info" | ForEach-Object {
        if ($_ -match '^(\w+)=(.*)$') {
            $varName = $matches[1]
            $varValue = $matches[2]
            if (-not (Get-Item "env:$varName" -ErrorAction SilentlyContinue)) {
                Set-Item "env:$varName" $varValue
            }
        }
    }
}

$RG  = if ($env:RG)  { $env:RG }  else { "nurse-triage-rg" }
$APP = if ($env:APP) { $env:APP } else { "nurse-triage-api" }

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Update Azure Container App Secrets" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Resource Group : $RG"
Write-Host " App            : $APP"
Write-Host ""

$SecretsList = @()
$Updated = $false

if ($env:DEEPSEEK_API_KEY) {
    $SecretsList += "deepseek-api-key=$($env:DEEPSEEK_API_KEY)"
    Write-Host "  [UPDATE] deepseek-api-key"
    $Updated = $true
}

if ($env:TWILIO_AUTH_TOKEN) {
    $SecretsList += "twilio-auth-token=$($env:TWILIO_AUTH_TOKEN)"
    Write-Host "  [UPDATE] twilio-auth-token"
    $Updated = $true
}

if ($env:DATABASE_URL) {
    $SecretsList += "database-url=$($env:DATABASE_URL)"
    Write-Host "  [UPDATE] database-url"
    $Updated = $true
}

if (-not $Updated) {
    Write-Host "  No secrets to update. Set env vars before running:" -ForegroundColor Yellow
    Write-Host '    $env:DEEPSEEK_API_KEY = "new-key"'
    Write-Host '    $env:TWILIO_AUTH_TOKEN = "new-token"'
    Write-Host '    $env:DATABASE_URL = "postgresql://..."'
    exit 0
}

Write-Host ""
Write-Host ">>> Updating secrets ..." -ForegroundColor Green
az containerapp secret set `
    --resource-group $RG `
    --name $APP `
    --secrets @SecretsList `
    --output none

Write-Host "    Secrets updated."

# Update non-secret env vars if provided
if ($env:CORS_ALLOWED_ORIGINS) {
    Write-Host ">>> Updating CORS_ALLOWED_ORIGINS ..." -ForegroundColor Green
    az containerapp update `
        --resource-group $RG `
        --name $APP `
        --set-env-vars "CORS_ALLOWED_ORIGINS=$($env:CORS_ALLOWED_ORIGINS)" `
        --output none
    Write-Host "    CORS updated."
}

if ($env:TWILIO_WEBHOOK_BASE_URL) {
    Write-Host ">>> Updating TWILIO_WEBHOOK_BASE_URL ..." -ForegroundColor Green
    az containerapp update `
        --resource-group $RG `
        --name $APP `
        --set-env-vars "TWILIO_WEBHOOK_BASE_URL=$($env:TWILIO_WEBHOOK_BASE_URL)" `
        --output none
    Write-Host "    Twilio webhook URL updated."
}

Write-Host ""
Write-Host "Done. The container will restart automatically to pick up new secrets." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
