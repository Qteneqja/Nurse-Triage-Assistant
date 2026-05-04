# =============================================================================
# azure-hotdeploy.ps1 — Manual hotfix deploy from Azure Cloud Shell
#
# Builds a new image via ACR Tasks (no kaniko, no blob storage) and
# updates the Container App in one shot.
#
# Usage:
#   ./scripts/azure-hotdeploy.ps1
#
# Requirements:
#   - Logged in to Azure CLI (az login)
#   - Service principal / identity has AcrPush on the ACR
#   - GitHub PAT with 'repo' (contents:read) scope for the private repo
# =============================================================================

#Requires -Version 7.0
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# CONFIGURATION — edit or override with environment variables
# ---------------------------------------------------------------------------
$RG = if ($env:RG) { $env:RG }           else { "nurse-triage-rg" }
$APP = if ($env:APP) { $env:APP }          else { "nurse-triage-api" }
$ACR = if ($env:ACR) { $env:ACR }          else { "nursetriageacr7351" }
$GITHUB_USER = if ($env:GITHUB_USER) { $env:GITHUB_USER }  else { "Qteneqja" }
$GITHUB_REPO = if ($env:GITHUB_REPO) { $env:GITHUB_REPO }  else { "Nurse-Triage-Assistant" }
$BRANCH = if ($env:BRANCH) { $env:BRANCH }       else { "main" }

# Optional: require a specific commit SHA on the tip of $BRANCH (leave empty to skip)
$REQUIRE_SHA = if ($env:REQUIRE_SHA) { $env:REQUIRE_SHA }  else { "" }

# ---------------------------------------------------------------------------
# HELPER
# ---------------------------------------------------------------------------
function Invoke-Step([string]$label, [scriptblock]$block) {
    Write-Host ""
    Write-Host ">>> $label" -ForegroundColor Cyan
    & $block
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        throw "Step failed: $label (exit $LASTEXITCODE)"
    }
}

# ---------------------------------------------------------------------------
# STEP 0: Pre-flight
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Nurse Triage Assistant — Cloud Shell hotdeploy" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Verify Azure login
$null = az account show 2>&1
if ($LASTEXITCODE -ne 0) { throw "Not logged in to Azure. Run: az login" }

$SUBSCRIPTION = az account show --query name -o tsv
Write-Host "Subscription : $SUBSCRIPTION"
Write-Host "Resource group: $RG"
Write-Host "Container App : $APP"
Write-Host "ACR           : $ACR"
Write-Host "Repo          : $GITHUB_USER/$GITHUB_REPO @ $BRANCH"
Write-Host ""

# ---------------------------------------------------------------------------
# STEP 1: Validate GitHub PAT
# ---------------------------------------------------------------------------
$GITHUB_PAT_SECURE = Read-Host "Paste GitHub PAT (needs 'repo' scope)" -AsSecureString
$BSTR = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($GITHUB_PAT_SECURE)
$GITHUB_PAT = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($BSTR)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR)

Write-Host "Checking GitHub token..."
try {
    $repoInfo = Invoke-RestMethod `
        -Headers @{ Authorization = "Bearer $GITHUB_PAT"; "User-Agent" = "Azure-CloudShell" } `
        -Uri "https://api.github.com/repos/$GITHUB_USER/$GITHUB_REPO"
    Write-Host "Repo confirmed: $($repoInfo.full_name) (private=$($repoInfo.private))"
}
catch {
    throw "GitHub PAT cannot read $GITHUB_USER/$GITHUB_REPO. Ensure the PAT has 'repo' scope and has not expired. Error: $_"
}

# ---------------------------------------------------------------------------
# STEP 2: Clone repo
# ---------------------------------------------------------------------------
$REPO_DIR = Join-Path $HOME "nurse-triage-deploy-$(Get-Date -Format 'yyyyMMddHHmmss')"
Write-Host ""
Write-Host ">>> Cloning $BRANCH into $REPO_DIR ..." -ForegroundColor Cyan

$BASIC = [Convert]::ToBase64String(
    [Text.Encoding]::ASCII.GetBytes("${GITHUB_USER}:${GITHUB_PAT}")
)
$GITHUB_PAT = $null  # clear from memory as soon as encoded

git -c http.extraheader="AUTHORIZATION: Basic $BASIC" `
    clone --branch $BRANCH --depth 1 `
    "https://github.com/$GITHUB_USER/$GITHUB_REPO.git" `
    $REPO_DIR

if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE). Check PAT scope and repo name." }

$BASIC = $null  # clear encoded credential

# ---------------------------------------------------------------------------
# STEP 3: Verify commit SHA (optional guard)
# ---------------------------------------------------------------------------
$LATEST_SHA = git -C $REPO_DIR rev-parse HEAD
$LATEST_LINE = git -C $REPO_DIR log --oneline -1
Write-Host "Tip commit: $LATEST_LINE"

if ($REQUIRE_SHA -and -not $LATEST_SHA.StartsWith($REQUIRE_SHA)) {
    Remove-Item -Recurse -Force $REPO_DIR -ErrorAction SilentlyContinue
    throw "$BRANCH tip is $LATEST_SHA but REQUIRE_SHA=$REQUIRE_SHA. Aborting."
}

# ---------------------------------------------------------------------------
# STEP 4: Build & push image via az acr build (replaces kaniko + blob storage)
#
#   az acr build streams Dockerfile build logs directly in your terminal,
#   uses your existing Azure identity (no ACR admin creds needed), and
#   pushes the image atomically — no intermediate blob storage required.
# ---------------------------------------------------------------------------
$TAG = "deploy-$(git -C $REPO_DIR log --format='%h' -1)-$(Get-Date -Format 'yyyyMMddHHmmss')"
$ACR_SERVER = az acr show --name $ACR --query loginServer -o tsv
$FULL_IMAGE = "$ACR_SERVER/nurse-triage:$TAG"

Invoke-Step "Build & push  →  $FULL_IMAGE" {
    az acr build `
        --registry $ACR `
        --image    "nurse-triage:$TAG" `
        --file     "$REPO_DIR/Dockerfile" `
        $REPO_DIR
}

# ---------------------------------------------------------------------------
# STEP 5: Update Container App
# ---------------------------------------------------------------------------
Invoke-Step "Update Container App '$APP'" {
    az containerapp update `
        --resource-group $RG `
        --name           $APP `
        --image          $FULL_IMAGE
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Deploy complete!" -ForegroundColor Green
Write-Host "  Image : $FULL_IMAGE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green

# Cleanup cloned repo
Remove-Item -Recurse -Force $REPO_DIR -ErrorAction SilentlyContinue
