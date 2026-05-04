# =============================================================================
# Post-Deployment Verification - Nurse Triage Assistant (PowerShell)
#
# 2-minute checklist to confirm the Azure deployment is healthy.
#
# Usage:
#   .\scripts\azure-verify.ps1
#   .\scripts\azure-verify.ps1 -BaseUrl "https://your-custom-domain.com"
# =============================================================================

param(
    [string]$BaseUrl = ""
)

$ErrorActionPreference = "Continue"

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

# Determine base URL
if (-not $BaseUrl) {
    if ($env:APP_FQDN) {
        $BaseUrl = "https://$($env:APP_FQDN)"
    }
    else {
        $RG = if ($env:RG) { $env:RG }  else { "nurse-triage-rg" }
        $APP = if ($env:APP) { $env:APP } else { "nurse-triage-api" }
        try {
            $FQDN = az containerapp show --resource-group $RG --name $APP --query properties.configuration.ingress.fqdn -o tsv 2>$null
            if ($FQDN) { $BaseUrl = "https://$FQDN" }
        }
        catch {}
    }
}

if (-not $BaseUrl) {
    Write-Host "ERROR: Cannot determine app URL." -ForegroundColor Red
    Write-Host '  Usage: .\scripts\azure-verify.ps1 -BaseUrl "https://<your-fqdn>"'
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ' Nurse Triage Assistant - Deployment Verification' -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Target: $BaseUrl"
Write-Host ""

$Pass = 0
$Fail = 0

function Test-Endpoint {
    param(
        [string]$Label,
        [string]$Url,
        [int]$ExpectedStatus = 200,
        [string]$Method = "GET"
    )

    $paddedLabel = $Label.PadRight(32)
    Write-Host "  $paddedLabel" -NoNewline

    try {
        if ($Method -eq "POST") {
            $response = Invoke-WebRequest -Uri $Url -Method POST -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
        }
        else {
            $response = Invoke-WebRequest -Uri $Url -Method GET -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
        }
        $statusCode = $response.StatusCode
    }
    catch {
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        else {
            $statusCode = 0
        }
    }

    if ($statusCode -eq $ExpectedStatus) {
        Write-Host ('PASS  (HTTP {0})' -f $statusCode) -ForegroundColor Green
        $script:Pass++
    }
    else {
        Write-Host ('FAIL  (HTTP {0}, expected {1})' -f $statusCode, $ExpectedStatus) -ForegroundColor Red
        $script:Fail++
    }
}

function Test-JsonField {
    param(
        [string]$Label,
        [string]$Url,
        [string]$Field,
        [string]$Expected
    )

    $paddedLabel = $Label.PadRight(32)
    Write-Host "  $paddedLabel" -NoNewline

    try {
        $response = Invoke-RestMethod -Uri $Url -Method GET -TimeoutSec 10 -ErrorAction Stop
        $actual = $response.$Field
    }
    catch {
        $actual = ""
    }

    if ($actual -eq $Expected) {
        Write-Host ('PASS  ({0}={1})' -f $Field, $actual) -ForegroundColor Green
        $script:Pass++
    }
    else {
        Write-Host ('FAIL  ({0}=''{1}'', expected ''{2}'')' -f $Field, $actual, $Expected) -ForegroundColor Red
        $script:Fail++
    }
}

# ---- 1. Endpoint Checks ----

Write-Host '1. Endpoint Checks'
Write-Host '   -----------------------------------------------'
Test-Endpoint -Label "Root endpoint"     -Url "$BaseUrl/"
Test-Endpoint -Label "Health (liveness)" -Url "$BaseUrl/health"
Test-Endpoint -Label "Readiness (DB)"    -Url "$BaseUrl/ready"
Test-Endpoint -Label "Metrics endpoint"  -Url "$BaseUrl/metrics"
Write-Host ""

# ---- 2. Response Validation ----

Write-Host '2. Response Validation'
Write-Host '   -----------------------------------------------'
Test-JsonField -Label "Health status" -Url "$BaseUrl/health" -Field "status"  -Expected "ok"
Test-JsonField -Label "Ready status"  -Url "$BaseUrl/ready"  -Field "status"  -Expected "ready"
Test-JsonField -Label "API version"   -Url "$BaseUrl/"       -Field "version" -Expected "5.0.0"
Write-Host ""

# ---- 3. Security Headers ----

Write-Host '3. Security Headers'
Write-Host '   -----------------------------------------------'
$paddedLabel = "X-Request-ID present".PadRight(32)
Write-Host "  $paddedLabel" -NoNewline
try {
    $resp = Invoke-WebRequest -Uri "$BaseUrl/health" -Method GET -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
    if ($resp.Headers["X-Request-ID"]) {
        Write-Host "PASS" -ForegroundColor Green
        $Pass++
    }
    else {
        Write-Host "FAIL  - missing X-Request-ID header" -ForegroundColor Red
        $Fail++
    }
}
catch {
    Write-Host "FAIL  - request error" -ForegroundColor Red
    $Fail++
}
Write-Host ""

# ---- 4. TLS ----

Write-Host '4. HTTPS/TLS'
Write-Host '   -----------------------------------------------'
$paddedLabel = "TLS certificate valid".PadRight(32)
Write-Host "  $paddedLabel" -NoNewline
try {
    $null = Invoke-WebRequest -Uri "$BaseUrl/health" -Method GET -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
    Write-Host "PASS" -ForegroundColor Green
    $Pass++
}
catch {
    Write-Host "FAIL  - TLS error or unreachable" -ForegroundColor Red
    $Fail++
}
Write-Host ""

# ---- 5. Twilio Webhook ----

Write-Host '5. Twilio Webhook (informational)'
Write-Host '   -----------------------------------------------'
$voiceUrl = "$BaseUrl/api/v1/voice/incoming"
$paddedLabel = "Voice webhook reachable".PadRight(32)
Write-Host "  $paddedLabel" -NoNewline
try {
    $resp = Invoke-WebRequest -Uri $voiceUrl -Method POST -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
    $statusCode = $resp.StatusCode
}
catch {
    if ($_.Exception.Response) {
        $statusCode = [int]$_.Exception.Response.StatusCode
    }
    else {
        $statusCode = 0
    }
}

if ($statusCode -in @(200, 400, 403, 422)) {
    Write-Host ('PASS  (HTTP {0} - route exists)' -f $statusCode) -ForegroundColor Green
    $Pass++
}
else {
    Write-Host ('WARN  (HTTP {0} - may need Twilio signature)' -f $statusCode) -ForegroundColor Yellow
}
Write-Host ""

# ---- Summary ----

$Total = $Pass + $Fail
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Results: $Pass/$Total passed" -ForegroundColor White
if ($Fail -eq 0) {
    Write-Host " Status: ALL CHECKS PASSED" -ForegroundColor Green
}
else {
    Write-Host " Status: $Fail CHECK(S) FAILED" -ForegroundColor Red
}
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host " Twilio Webhook URL (set in Twilio Console):" -ForegroundColor White
Write-Host "   POST $BaseUrl/api/v1/voice/incoming" -ForegroundColor Yellow
Write-Host ""

exit $Fail
