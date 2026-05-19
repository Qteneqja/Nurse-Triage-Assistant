<# 
.SYNOPSIS
    Shared safety helpers for Azure security scripts.

.DESCRIPTION
    Provides redaction and Azure CLI guard helpers. Functions in this file are
    intentionally small and output-safe; they must never print secret values.
#>

Set-StrictMode -Version Latest

$Script:SensitiveNamePattern = '(?i)(key|token|secret|password|passwd|pwd|connection|connectionstring|connection_string|database_url|database-url|sid|dsn|credential|auth)'

function Test-SensitiveName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    return ($Name -match $Script:SensitiveNamePattern)
}

function Redact-SensitiveValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [AllowNull()]
        [object]$Value
    )

    if ($null -eq $Value) {
        return $null
    }

    if (Test-SensitiveName -Name $Name) {
        return "[REDACTED]"
    }

    return $Value
}

function Assert-AzCliReady {
    param(
        [string]$SubscriptionId
    )

    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw "Azure CLI 'az' is not installed or not on PATH."
    }

    $accountId = (& az account show --query id -o tsv 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $accountId) {
        throw "Azure CLI is not logged in. Run 'az login' and select the production subscription."
    }

    if ($SubscriptionId) {
        & az account set --subscription $SubscriptionId --output none
        if ($LASTEXITCODE -ne 0) {
            throw "Could not select subscription '$SubscriptionId'."
        }
        $accountId = (& az account show --query id -o tsv 2>$null)
    }

    return $accountId
}

function ConvertFrom-JsonOrNull {
    param(
        [AllowNull()]
        [string]$Text
    )

    if (-not $Text) {
        return $null
    }

    return $Text | ConvertFrom-Json
}
