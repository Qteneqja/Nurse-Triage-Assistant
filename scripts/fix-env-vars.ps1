<#
.SYNOPSIS
    Deprecated legacy Container App env-var repair script.

.DESCRIPTION
    This script used to fetch full Container App configuration and print current
    environment values. That behavior is unsafe for production because
    Container App environment values can include secrets.

    Production credential rotation has already been completed by the operator.
    Do not use this legacy script for remediation. Use the safe identity and
    Key Vault tooling instead.

.EXAMPLE
    .\scripts\azure-remediate-identity-secrets.ps1 -SubscriptionId "<subscription-id>" -KeyVaultName "<key-vault-name>" -DryRun

.EXAMPLE
    .\scripts\azure-security-verify.ps1 -SubscriptionId "<subscription-id>" -KeyVaultName "<key-vault-name>"
#>

$ErrorActionPreference = "Stop"

Write-Host "ERROR: scripts/fix-env-vars.ps1 is deprecated and disabled." -ForegroundColor Red
Write-Host "Reason: the legacy implementation could print plaintext Container App env values."
Write-Host "Use the safe remediation and verification scripts instead:"
Write-Host '  .\scripts\azure-remediate-identity-secrets.ps1 -SubscriptionId "<subscription-id>" -KeyVaultName "<key-vault-name>" -DryRun'
Write-Host '  .\scripts\azure-security-verify.ps1 -SubscriptionId "<subscription-id>" -KeyVaultName "<key-vault-name>"'
exit 2
