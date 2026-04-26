# =============================================================================
# Azure Blob Storage - Create Monthly Report Folders for 2026
#
# Creates virtual directory placeholders (.keep files) in the
# triage-reports container for months 03-March through 12-December 2026.
#
# Prerequisites:
#   - Azure CLI installed: winget install Microsoft.AzureCLI
#   - Logged in: az login
#   - AZURE_STORAGE_CONNECTION_STRING set (or pass -ConnectionString)
#
# Usage:
#   .\scripts\azure-create-blob-folders.ps1
#   .\scripts\azure-create-blob-folders.ps1 -ConnectionString "<conn-string>"
#   .\scripts\azure-create-blob-folders.ps1 -ContainerName "triage-reports"
# =============================================================================

param(
    [string]$ConnectionString = "",
    [string]$ContainerName = "triage-reports"
)

$ErrorActionPreference = "Stop"

# Resolve connection string
if (-not $ConnectionString) {
    $ConnectionString = $env:AZURE_STORAGE_CONNECTION_STRING
}
if (-not $ConnectionString) {
    Write-Host 'ERROR: Azure Storage connection string not found.' -ForegroundColor Red
    Write-Host '  Set AZURE_STORAGE_CONNECTION_STRING env var or pass -ConnectionString'
    exit 1
}

# Month definitions (matching local report folder naming convention)
$months = @(
    '03-March',
    '04-April',
    '05-May',
    '06-June',
    '07-July',
    '08-August',
    '09-September',
    '10-October',
    '11-November',
    '12-December'
)

Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '  Azure Blob Storage - Create 2026 Monthly Report Folders'   -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ''
Write-Host ('Container : ' + $ContainerName)
Write-Host ('Folders   : ' + $months.Count + ' months (03-March .. 12-December)')
Write-Host ''

# Ensure container exists
Write-Host ('[1/3] Checking container ' + $ContainerName + ' exists...') -ForegroundColor Yellow
$containerExists = az storage container exists `
    --name $ContainerName `
    --connection-string $ConnectionString `
    --query 'exists' -o tsv 2>$null

if ($containerExists -ne 'true') {
    Write-Host ('  Creating container ' + $ContainerName + '...') -ForegroundColor Yellow
    az storage container create `
        --name $ContainerName `
        --connection-string $ConnectionString `
        --output none
    Write-Host '  Container created.' -ForegroundColor Green
}
else {
    Write-Host '  Container already exists.' -ForegroundColor Green
}

# Create virtual folder placeholders
Write-Host ''
Write-Host '[2/3] Creating monthly folder placeholders...' -ForegroundColor Yellow

$created = 0
$skipped = 0

foreach ($month in $months) {
    $blobName = '2026/' + $month + '/.keep'

    # Check if placeholder already exists
    $exists = az storage blob exists `
        --container-name $ContainerName `
        --name $blobName `
        --connection-string $ConnectionString `
        --query 'exists' -o tsv 2>$null

    if ($exists -eq 'true') {
        Write-Host ('  [SKIP] 2026/' + $month + '/ (already exists)') -ForegroundColor DarkGray
        $skipped++
        continue
    }

    # Upload an empty .keep placeholder to create the virtual directory
    $emptyFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($emptyFile, '')

    az storage blob upload `
        --container-name $ContainerName `
        --name $blobName `
        --file $emptyFile `
        --connection-string $ConnectionString `
        --content-type 'text/plain' `
        --output none 2>$null

    Remove-Item $emptyFile -Force -ErrorAction SilentlyContinue

    Write-Host ('  [OK]   2026/' + $month + '/') -ForegroundColor Green
    $created++
}

# Summary
Write-Host ''
Write-Host '[3/3] Done!' -ForegroundColor Yellow
Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '  Results:'
Write-Host ('    Created : ' + $created + ' folder(s)') -ForegroundColor Green
Write-Host ('    Skipped : ' + $skipped + ' folder(s) (already existed)') -ForegroundColor DarkGray
Write-Host ('    Total   : ' + $months.Count + ' month(s)')
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ''
Write-Host 'Blob path structure:' -ForegroundColor White
foreach ($month in $months) {
    Write-Host ('  ' + $ContainerName + '/2026/' + $month + '/')
}
Write-Host ''
