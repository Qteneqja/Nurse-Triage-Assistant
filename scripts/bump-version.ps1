# =============================================================================
# Version Bump Script - Nurse Triage Assistant
#
# Manages semantic versioning (MAJOR.MINOR.PATCH) from the VERSION file.
#
# Container revisions within the current major version use:
#   v5.0.0, v5.0.1, v5.1.0, v5.1.1, ...
#
# When a complete new version is finished, bump major:
#   v5.x.xx -> v6.0.0
#
# Usage:
#   .\scripts\bump-version.ps1 -Part patch    # 5.0.0 -> 5.0.1
#   .\scripts\bump-version.ps1 -Part minor    # 5.0.1 -> 5.1.0
#   .\scripts\bump-version.ps1 -Part major    # 5.1.0 -> 6.0.0
#   .\scripts\bump-version.ps1 -Set "5.2.3"   # Set exact version
#   .\scripts\bump-version.ps1               # Show current version
# =============================================================================

param(
    [ValidateSet('major', 'minor', 'patch', '')]
    [string]$Part = '',
    [string]$Set = ''
)

$ErrorActionPreference = 'Stop'

$VERSION_FILE = Join-Path $PSScriptRoot '..\VERSION'

if (-not (Test-Path $VERSION_FILE)) {
    Write-Host 'ERROR: VERSION file not found at project root.' -ForegroundColor Red
    exit 1
}

$currentVersion = (Get-Content $VERSION_FILE -Raw).Trim()
Write-Host ''
Write-Host ('Current version: v' + $currentVersion) -ForegroundColor Cyan

# If no arguments, just display current version
if (-not $Part -and -not $Set) {
    Write-Host ''
    Write-Host 'Usage:' -ForegroundColor Yellow
    Write-Host '  .\scripts\bump-version.ps1 -Part patch    # bump patch  (5.0.0 -> 5.0.1)'
    Write-Host '  .\scripts\bump-version.ps1 -Part minor    # bump minor  (5.0.1 -> 5.1.0)'
    Write-Host '  .\scripts\bump-version.ps1 -Part major    # bump major  (5.1.0 -> 6.0.0)'
    Write-Host '  .\scripts\bump-version.ps1 -Set "5.2.3"   # set exact version'
    Write-Host ''
    exit 0
}

# Set exact version
if ($Set) {
    if ($Set -notmatch '^\d+\.\d+\.\d+$') {
        Write-Host ('ERROR: Invalid version format: ' + $Set + ' (expected MAJOR.MINOR.PATCH)') -ForegroundColor Red
        exit 1
    }
    $newVersion = $Set
}
else {
    # Parse current version
    $parts = $currentVersion -split '\.'
    if ($parts.Count -ne 3) {
        Write-Host ('ERROR: Cannot parse current version: ' + $currentVersion) -ForegroundColor Red
        exit 1
    }

    [int]$major = $parts[0]
    [int]$minor = $parts[1]
    [int]$patch = $parts[2]

    switch ($Part) {
        'major' {
            $major++
            $minor = 0
            $patch = 0
        }
        'minor' {
            $minor++
            $patch = 0
        }
        'patch' {
            $patch++
        }
    }

    $newVersion = "$major.$minor.$patch"
}

# Write new version
$newVersion | Set-Content $VERSION_FILE -NoNewline -Encoding utf8
# Add trailing newline
Add-Content $VERSION_FILE '' -Encoding utf8

Write-Host ('New version:     v' + $newVersion) -ForegroundColor Green
Write-Host ''
Write-Host 'Updated files:' -ForegroundColor Yellow
Write-Host '  VERSION                          (source of truth)'
Write-Host '  src/config.py                    (reads VERSION at startup)'
Write-Host '  src/main.py                      (FastAPI app + /root endpoint)'
Write-Host '  scripts/azure-deploy.ps1         (IMAGE_TAG default)'
Write-Host '  scripts/azure-deploy.sh          (IMAGE_TAG default)'
Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Yellow
Write-Host ('  1. Commit: git add VERSION; git commit -m "Bump version to v' + $newVersion + '"')
Write-Host '  2. Tag:    git tag v' -NoNewline
Write-Host $newVersion
Write-Host '  3. Deploy: .\scripts\azure-deploy.ps1'
Write-Host ''
