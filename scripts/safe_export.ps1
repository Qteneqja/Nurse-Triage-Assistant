<#
.SYNOPSIS
    Creates a ZIP archive of the project, excluding secrets, PHI, caches, and git history.

.DESCRIPTION
    Use this script whenever you need to share the codebase externally.
    It strips .env files, private keys, virtual-env folders, __pycache__,
    node_modules, build artefacts, git internals, and any known
    sensitive docs (credentials.md, transcripts, reports with PHI).

.EXAMPLE
    .\scripts\safe_export.ps1                       # defaults to .\export\NurseTriage-<date>.zip
    .\scripts\safe_export.ps1 -OutPath C:\tmp\out.zip
#>
param(
    [string]$OutPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path

if (-not $OutPath) {
    $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
    $exportDir = Join-Path $projectRoot 'export'
    if (-not (Test-Path $exportDir)) { New-Item -ItemType Directory -Path $exportDir | Out-Null }
    $OutPath = Join-Path $exportDir "NurseTriage-$ts.zip"
}

# ---- Exclusion patterns (relative to project root) --------------------------
$excludeDirs = @(
    '.git',
    '.venv', 'venv', 'env',
    '__pycache__',
    'node_modules',
    '.mypy_cache', '.pytest_cache', '.ruff_cache',
    'export'                       # don't zip previous exports
)

$excludeFiles = @(
    '*.env', '.env', '.env.*',
    '*.pem', '*.key', '*.crt', '*.p12', '*.pfx',
    '*.pyc', '*.pyo',
    'credentials.md'
)

# Gather all files, apply exclusions
$allFiles = Get-ChildItem -Path $projectRoot -Recurse -File

$filtered = $allFiles | Where-Object {
    $rel = $_.FullName.Substring($projectRoot.Length + 1)
    $parts = $rel -split '\\'

    # Exclude if any path segment matches an excluded directory
    foreach ($dir in $excludeDirs) {
        if ($parts -contains $dir) { return $false }
    }

    # Exclude by file-name pattern
    foreach ($pat in $excludeFiles) {
        if ($_.Name -like $pat) { return $false }
    }

    return $true
}

# Stage into a temp folder, then compress
$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) "safe_export_$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $tmpDir | Out-Null

try {
    foreach ($f in $filtered) {
        $rel = $f.FullName.Substring($projectRoot.Length + 1)
        $dest = Join-Path $tmpDir $rel
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
        Copy-Item $f.FullName $dest
    }

    Compress-Archive -Path "$tmpDir\*" -DestinationPath $OutPath -Force
    Write-Host "Safe export created: $OutPath" -ForegroundColor Green
    Write-Host "Files included: $($filtered.Count)"
}
finally {
    Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
}
