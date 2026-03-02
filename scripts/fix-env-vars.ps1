#!/usr/bin/env pwsh
# Fix missing environment variables on Azure Container Apps deployment
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host "=== Fixing Container App Environment Variables ===" -ForegroundColor Cyan

# Get access token and subscription
Write-Host "Getting Azure credentials..."
$tokenJson = az account get-access-token --resource https://management.azure.com 2>$null
$token = ($tokenJson | ConvertFrom-Json).accessToken
$subId = (az account show 2>$null | ConvertFrom-Json).id

if (-not $token -or -not $subId) {
    Write-Host "ERROR: Could not get Azure credentials. Run 'az login' first." -ForegroundColor Red
    exit 1
}
Write-Host "  Subscription: $subId"
Write-Host "  Token: OK (length $($token.Length))"

$rg = "nurse-triage-eastus-rg"
$appName = "nurse-triage-api"
$apiVer = "2024-03-01"
$baseUri = "https://management.azure.com/subscriptions/$subId/resourceGroups/$rg/providers/Microsoft.App/containerApps/$appName"
$headers = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }

# Step 1: GET current config
Write-Host "`nStep 1: Fetching current Container App config..."
try {
    $current = Invoke-RestMethod -Uri "$baseUri`?api-version=$apiVer" -Headers $headers -Method Get -TimeoutSec 30
    Write-Host "  Current env vars:" -ForegroundColor Green
    foreach ($e in $current.properties.template.containers[0].env) {
        $val = if ($e.value) { $e.value } elseif ($e.secretRef) { "(secret: $($e.secretRef))" } else { "(empty)" }
        Write-Host "    $($e.name) = $val"
    }
}
catch {
    Write-Host "ERROR fetching config: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Inner: $($_.Exception.InnerException.Message)" -ForegroundColor Red
    exit 1
}

# Step 2: Build the updated env vars list (keep existing secrets, add missing env vars)
Write-Host "`nStep 2: Building updated environment variables..."

$existingEnv = $current.properties.template.containers[0].env

# New env vars to add
$newEnvVars = @(
    @{ name = "APP_ENV"; value = "production" },
    @{ name = "ENVIRONMENT"; value = "production" },
    @{ name = "STORAGE_BACKEND"; value = "postgres" },
    @{ name = "LOG_FORMAT"; value = "json" },
    @{ name = "TRUST_PROXY_HEADERS"; value = "true" },
    @{ name = "TWILIO_VALIDATE_SIGNATURE"; value = "true" },
    @{ name = "PROTOCOL_VERSION"; value = "v1" },
    @{ name = "CONFIDENCE_MIN_THRESHOLD"; value = "0.60" },
    @{ name = "REDFLAG_SCORE_THRESHOLD"; value = "10" },
    @{ name = "RATE_LIMIT"; value = "60/minute" },
    @{ name = "RUN_MIGRATIONS_ON_STARTUP"; value = "false" },
    @{ name = "TWILIO_WEBHOOK_BASE_URL"; value = "https://$($current.properties.configuration.ingress.fqdn)" }
)

# Merge: keep existing, add new (don't overwrite existing)
$existingNames = @($existingEnv | ForEach-Object { $_.name })
$mergedEnv = [System.Collections.ArrayList]@()

# Keep all existing
foreach ($e in $existingEnv) {
    $obj = @{ name = $e.name }
    if ($e.secretRef) { $obj["secretRef"] = $e.secretRef }
    elseif ($null -ne $e.value) { $obj["value"] = $e.value }
    else { $obj["value"] = "" }
    [void]$mergedEnv.Add($obj)
}

# Add new ones that don't already exist
foreach ($n in $newEnvVars) {
    if ($n.name -notin $existingNames) {
        [void]$mergedEnv.Add(@{ name = $n.name; value = $n.value })
        Write-Host "  + Adding: $($n.name) = $($n.value)" -ForegroundColor Green
    }
    else {
        Write-Host "  ~ Exists: $($n.name) (keeping current value)" -ForegroundColor Yellow
    }
}

# Step 3: PATCH the container app
Write-Host "`nStep 3: Applying update..."

# Build the PATCH body - only update the container env vars
$container = $current.properties.template.containers[0]
$patchBody = @{
    properties = @{
        template = @{
            containers = @(
                @{
                    name      = $container.name
                    image     = $container.image
                    resources = @{
                        cpu    = $container.resources.cpu
                        memory = $container.resources.memory
                    }
                    env       = $mergedEnv.ToArray()
                }
            )
        }
    }
} | ConvertTo-Json -Depth 10

try {
    $result = Invoke-RestMethod -Uri "$baseUri`?api-version=$apiVer" -Headers $headers -Method Patch -Body $patchBody -TimeoutSec 60
    Write-Host "  Update submitted! Provisioning state: $($result.properties.provisioningState)" -ForegroundColor Green
}
catch {
    Write-Host "ERROR applying update: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.Exception.Response) {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $body = $reader.ReadToEnd()
        Write-Host "Response body: $body" -ForegroundColor Red
    }
    exit 1
}

# Step 4: Verify
Write-Host "`nStep 4: Verifying health..."
Start-Sleep -Seconds 5
try {
    $health = Invoke-RestMethod -Uri "https://$($current.properties.configuration.ingress.fqdn)/health" -TimeoutSec 10
    Write-Host "  Health: $($health.status)" -ForegroundColor Green
}
catch {
    Write-Host "  Health check pending (app may be restarting)..." -ForegroundColor Yellow
}

Write-Host "`n=== Done ===" -ForegroundColor Cyan
Write-Host "The Container App will restart with the new environment variables."
Write-Host "Wait ~30 seconds, then check:"
Write-Host "  https://$($current.properties.configuration.ingress.fqdn)/health"
Write-Host "  https://$($current.properties.configuration.ingress.fqdn)/ready"
