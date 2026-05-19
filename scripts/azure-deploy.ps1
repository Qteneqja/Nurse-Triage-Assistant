# =============================================================================
# Azure Deployment Script - Nurse Triage Assistant v5.0.0 (PowerShell)
#
# Creates all Azure resources and deploys the API to Container Apps.
#
# Prerequisites:
#   - Azure CLI installed: winget install Microsoft.AzureCLI
#   - Logged in: az login
#
# Usage:
#   # 1. Edit the CONFIGURATION section below (or set env vars)
#   # 2. Run:
#   .\scripts\azure-deploy.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

# =============================================================================
# CONFIGURATION - Edit these before running
# =============================================================================

$RG = if ($env:RG) { $env:RG }          else { "nurse-triage-rg" }
$LOC = if ($env:LOC) { $env:LOC }         else { "canadacentral" }
$RAND_SUFFIX = Get-Random -Minimum 1000 -Maximum 9999
$ACR = if ($env:ACR) { $env:ACR }         else { "nursetriageacr$RAND_SUFFIX" }
$PG = if ($env:PG) { $env:PG }          else { "nurse-triage-pg-$RAND_SUFFIX" }
$APP = if ($env:APP) { $env:APP }         else { "nurse-triage-api" }
$ENV_NAME = if ($env:ENV_NAME) { $env:ENV_NAME }    else { "nurse-triage-aca-env" }

# PostgreSQL credentials
$PG_ADMIN_USER = if ($env:PG_ADMIN_USER) { $env:PG_ADMIN_USER } else { "triageadmin" }
$PG_ADMIN_PASS = $env:PG_ADMIN_PASS   # REQUIRED
$PG_DB_NAME = if ($env:PG_DB_NAME) { $env:PG_DB_NAME }    else { "triage_db" }

# Application secrets (REQUIRED)
$DEEPSEEK_API_KEY = $env:DEEPSEEK_API_KEY
$TWILIO_AUTH_TOKEN = $env:TWILIO_AUTH_TOKEN

# Image tag — reads from VERSION file (single source of truth)
$VERSION_FILE = Join-Path $PSScriptRoot '..\VERSION'
if (Test-Path $VERSION_FILE) {
    $DEFAULT_TAG = (Get-Content $VERSION_FILE -Raw).Trim()
}
else {
    $DEFAULT_TAG = '5.0.0'
}
$IMAGE_TAG = if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { $DEFAULT_TAG }

# Optional: custom domain for CORS
$DASHBOARD_DOMAIN = $env:DASHBOARD_DOMAIN

# =============================================================================
# PRE-FLIGHT CHECKS
# =============================================================================

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ' Nurse Triage Assistant - Azure Deployment' -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check Azure CLI
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Azure CLI (az) is not installed." -ForegroundColor Red
    Write-Host "  Install with: winget install Microsoft.AzureCLI" -ForegroundColor Yellow
    Write-Host "  Then restart your terminal and run: az login" -ForegroundColor Yellow
    exit 1
}

# Check login
try {
    $null = az account show 2>$null | ConvertFrom-Json
}
catch {
    Write-Host "ERROR: Not logged in to Azure. Run 'az login' first." -ForegroundColor Red
    exit 1
}

# Validate required secrets
if (-not $PG_ADMIN_PASS) {
    Write-Host "ERROR: PG_ADMIN_PASS is not set." -ForegroundColor Red
    Write-Host '  $env:PG_ADMIN_PASS = "<postgres-admin-password>"' -ForegroundColor Yellow
    exit 1
}
if (-not $DEEPSEEK_API_KEY) {
    Write-Host "ERROR: DEEPSEEK_API_KEY is not set." -ForegroundColor Red
    Write-Host '  $env:DEEPSEEK_API_KEY = "<deepseek-api-key>"' -ForegroundColor Yellow
    exit 1
}
if (-not $TWILIO_AUTH_TOKEN) {
    Write-Host "WARNING: TWILIO_AUTH_TOKEN is not set. Twilio signature validation will fail." -ForegroundColor Yellow
    Write-Host "  Set it now or update later with: .\scripts\azure-update-secrets.ps1" -ForegroundColor Yellow
    Write-Host ""
}

$Subscription = az account show --query name -o tsv
Write-Host "Subscription  : $Subscription"
Write-Host "Resource Group : $RG"
Write-Host "Location       : $LOC"
Write-Host "ACR            : $ACR"
Write-Host "PostgreSQL     : $PG"
Write-Host "Container App  : $APP"
Write-Host "Environment    : $ENV_NAME"
Write-Host ""

$confirm = Read-Host "Continue? (y/N)"
if ($confirm -ne "y" -and $confirm -ne "Y" -and $confirm -ne "yes") {
    Write-Host "Aborted."
    exit 0
}

# =============================================================================
# STEP 1: Resource Group
# =============================================================================

Write-Host ""
Write-Host ('>>> Step 1/6: Creating Resource Group ''{0}'' in ''{1}'' ...' -f $RG, $LOC) -ForegroundColor Green
az group create --name $RG --location $LOC --output none
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED" -ForegroundColor Red; exit 1 }
Write-Host "    Resource Group ready."

# =============================================================================
# STEP 2: Azure Container Registry (ACR)
# =============================================================================

Write-Host ""
Write-Host ('>>> Step 2/6: Creating Container Registry ''{0}'' ...' -f $ACR) -ForegroundColor Green
az acr create `
    --resource-group $RG `
    --name $ACR `
    --sku Basic `
    --admin-enabled true `
    --output none
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED" -ForegroundColor Red; exit 1 }

$ACR_SERVER = az acr show --name $ACR --query loginServer -o tsv
Write-Host "    ACR login server: $ACR_SERVER"

# Build the Docker image directly in ACR (no local Docker needed)
Write-Host "    Building image ${ACR_SERVER}/nurse-triage:${IMAGE_TAG} in ACR ..."
az acr build `
    --registry $ACR `
    --image "nurse-triage:$IMAGE_TAG" `
    . `
    --no-logs
if ($LASTEXITCODE -ne 0) { Write-Host "ACR build FAILED" -ForegroundColor Red; exit 1 }

Write-Host "    Image built and pushed: ${ACR_SERVER}/nurse-triage:${IMAGE_TAG}"

# =============================================================================
# STEP 3: PostgreSQL Flexible Server
# =============================================================================

Write-Host ""
Write-Host ('>>> Step 3/6: Creating PostgreSQL Flexible Server ''{0}'' ...' -f $PG) -ForegroundColor Green
az postgres flexible-server create `
    --resource-group $RG `
    --name $PG `
    --location $LOC `
    --admin-user $PG_ADMIN_USER `
    --admin-password $PG_ADMIN_PASS `
    --database-name $PG_DB_NAME `
    --sku-name Standard_B1ms `
    --tier Burstable `
    --storage-size 32 `
    --version 16 `
    --public-access 0.0.0.0 `
    --yes `
    --output none
if ($LASTEXITCODE -ne 0) { Write-Host "PostgreSQL creation FAILED" -ForegroundColor Red; exit 1 }

$PG_FQDN = az postgres flexible-server show `
    --resource-group $RG `
    --name $PG `
    --query fullyQualifiedDomainName -o tsv

# Connection string with SSL (Azure requires sslmode=require at minimum)
$DATABASE_URL = "postgresql://${PG_ADMIN_USER}:${PG_ADMIN_PASS}@${PG_FQDN}:5432/${PG_DB_NAME}?sslmode=require"

Write-Host "    PostgreSQL FQDN  : $PG_FQDN"
Write-Host "    Database          : $PG_DB_NAME"
Write-Host "    Connection string : (stored securely - not printed)"

# =============================================================================
# STEP 4: Container Apps Environment
# =============================================================================

Write-Host ""
Write-Host ('>>> Step 4/6: Creating Container Apps Environment ''{0}'' ...' -f $ENV_NAME) -ForegroundColor Green

# Ensure the containerapp extension is installed
az extension add --name containerapp --upgrade --yes 2>$null

az containerapp env create `
    --resource-group $RG `
    --name $ENV_NAME `
    --location $LOC `
    --output none
if ($LASTEXITCODE -ne 0) { Write-Host "ACA Environment creation FAILED" -ForegroundColor Red; exit 1 }

Write-Host "    Container Apps Environment ready."

# =============================================================================
# STEP 5: Deploy Container App
# =============================================================================

Write-Host ""
Write-Host ('>>> Step 5/6: Creating Container App ''{0}'' ...' -f $APP) -ForegroundColor Green

# Get ACR credentials
$ACR_USERNAME = az acr credential show --name $ACR --query username -o tsv
$ACR_PASSWORD = az acr credential show --name $ACR --query "passwords[0].value" -o tsv

# Build CORS origin string
$CORS_ORIGINS = ""
if ($DASHBOARD_DOMAIN) {
    $CORS_ORIGINS = "https://$DASHBOARD_DOMAIN"
}

az containerapp create `
    --resource-group $RG `
    --name $APP `
    --environment $ENV_NAME `
    --image "${ACR_SERVER}/nurse-triage:${IMAGE_TAG}" `
    --ingress external `
    --target-port 8000 `
    --min-replicas 1 `
    --max-replicas 3 `
    --cpu 1.0 `
    --memory 2.0Gi `
    --registry-server $ACR_SERVER `
    --registry-username $ACR_USERNAME `
    --registry-password $ACR_PASSWORD `
    --output none
if ($LASTEXITCODE -ne 0) { Write-Host "Container App creation FAILED" -ForegroundColor Red; exit 1 }

Write-Host "    Container App created."

# =============================================================================
# STEP 6: Set secrets + environment variables
# =============================================================================

Write-Host ""
Write-Host '>>> Step 6/6: Configuring secrets and environment variables ...' -ForegroundColor Green

# Build secrets list
$SecretsList = @("deepseek-api-key=$DEEPSEEK_API_KEY", "database-url=$DATABASE_URL")
if ($TWILIO_AUTH_TOKEN) {
    $SecretsList += "twilio-auth-token=$TWILIO_AUTH_TOKEN"
}

az containerapp secret set `
    --resource-group $RG `
    --name $APP `
    --secrets @SecretsList `
    --output none
if ($LASTEXITCODE -ne 0) { Write-Host "Secret configuration FAILED" -ForegroundColor Red; exit 1 }

# Get the FQDN for Twilio webhook config
$APP_FQDN = az containerapp show `
    --resource-group $RG `
    --name $APP `
    --query properties.configuration.ingress.fqdn -o tsv

# Build environment variable list
$EnvVarsList = @(
    "APP_ENV=production",
    "ENVIRONMENT=production",
    "STORAGE_BACKEND=postgres",
    "LOG_FORMAT=json",
    "TRUST_PROXY_HEADERS=true",
    "TWILIO_VALIDATE_SIGNATURE=true",
    "PROTOCOL_VERSION=v1",
    "CONFIDENCE_MIN_THRESHOLD=0.60",
    "REDFLAG_SCORE_THRESHOLD=10",
    "RATE_LIMIT=60/minute",
    "RUN_MIGRATIONS_ON_STARTUP=false",
    "DEEPSEEK_API_KEY=secretref:deepseek-api-key",
    "DATABASE_URL=secretref:database-url",
    "TWILIO_WEBHOOK_BASE_URL=https://$APP_FQDN"
)
if ($TWILIO_AUTH_TOKEN) {
    $EnvVarsList += "TWILIO_AUTH_TOKEN=secretref:twilio-auth-token"
}
if ($CORS_ORIGINS) {
    $EnvVarsList += "CORS_ALLOWED_ORIGINS=$CORS_ORIGINS"
}

az containerapp update `
    --resource-group $RG `
    --name $APP `
    --set-env-vars @EnvVarsList `
    --output none
if ($LASTEXITCODE -ne 0) { Write-Host "Env var configuration FAILED" -ForegroundColor Red; exit 1 }

Write-Host "    Secrets and env vars configured."

# =============================================================================
# SUMMARY
# =============================================================================

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " DEPLOYMENT COMPLETE" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host " App URL         : https://$APP_FQDN" -ForegroundColor White
Write-Host " Health check    : https://$APP_FQDN/health" -ForegroundColor White
Write-Host " Readiness check : https://$APP_FQDN/ready" -ForegroundColor White
Write-Host ""
Write-Host " Twilio Voice Webhook:" -ForegroundColor White
Write-Host "   POST https://$APP_FQDN/api/v1/voice/incoming" -ForegroundColor Yellow
Write-Host ""
Write-Host " NEXT STEPS:" -ForegroundColor Green
Write-Host "   1. Run migrations:"
Write-Host "      .\scripts\azure-run-migration.ps1"
Write-Host ""
Write-Host "   2. Verify deployment:"
Write-Host "      .\scripts\azure-verify.ps1"
Write-Host ""
Write-Host "   3. Configure Twilio webhook:"
Write-Host '      In Twilio Console -> Phone Number -> Voice and Fax'
Write-Host "      Set 'A CALL COMES IN' webhook to:"
Write-Host "        POST https://$APP_FQDN/api/v1/voice/incoming" -ForegroundColor Yellow
Write-Host ""
Write-Host "   4. (Optional) Add custom domain:"
Write-Host "      az containerapp hostname add -g $RG -n $APP --hostname api.yourdomain.com"
Write-Host ""

# Save deployment info for other scripts
$deployInfo = @(
    "RG=$RG",
    "LOC=$LOC",
    "ACR=$ACR",
    "ACR_SERVER=$ACR_SERVER",
    "PG=$PG",
    "PG_FQDN=$PG_FQDN",
    "PG_DB_NAME=$PG_DB_NAME",
    "APP=$APP",
    "ENV_NAME=$ENV_NAME",
    "APP_FQDN=$APP_FQDN",
    "IMAGE_TAG=$IMAGE_TAG"
)
$deployInfo | Set-Content -Path ".azure-deploy-info" -Encoding UTF8

Write-Host " Deployment info saved to .azure-deploy-info" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan
