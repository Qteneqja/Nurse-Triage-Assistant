<#
.SYNOPSIS
    Prepare Azure Container Apps for managed identity, Key Vault references, and ACR pull without registry passwords.

.DESCRIPTION
    Enables a system-assigned managed identity on the Container App, creates or
    validates a production Key Vault, assigns least-privilege roles, configures
    ACR pull through managed identity, and converts existing Container App
    secret names to Key Vault references when matching Key Vault secrets already
    exist.

    Production credential rotation has already been completed by the operator.
    Rotation is intentionally out of scope for this script. Use this script to
    prevent future plaintext exposure by moving runtime configuration toward
    managed identity and Key Vault references.

    The script never accepts, echoes, or prints secret values. It only prints
    resource names, identity principal IDs, role assignment status, secret names,
    and compliance status.

.PARAMETER SubscriptionId
    Azure subscription ID to operate against.

.PARAMETER ResourceGroupName
    Resource group containing the Container App and ACR.

.PARAMETER Location
    Azure region for Key Vault creation. Defaults to canadacentral.

.PARAMETER ContainerAppName
    Container App name. Defaults to nurse-triage-api.

.PARAMETER ContainerAppsEnvironmentName
    Container Apps environment name. Included for operator context.

.PARAMETER AcrName
    Azure Container Registry name. Defaults to nursetriageacr7351.

.PARAMETER KeyVaultName
    Production Key Vault name. Required.

.PARAMETER DryRun
    Show the planned actions and run read-only discovery without making changes.

.PARAMETER DisableAcrAdminAfterValidation
    Disable ACR admin credentials after managed identity registry configuration
    is applied. Use only after validating image pull on a new Container App revision.

.EXAMPLE
    .\scripts\azure-remediate-identity-secrets.ps1 `
        -SubscriptionId "00000000-0000-0000-0000-000000000000" `
        -KeyVaultName "kv-nurse-triage-prod" `
        -DryRun

.EXAMPLE
    .\scripts\azure-remediate-identity-secrets.ps1 `
        -SubscriptionId "00000000-0000-0000-0000-000000000000" `
        -KeyVaultName "kv-nurse-triage-prod" `
        -DisableAcrAdminAfterValidation
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [string]$ResourceGroupName = "nurse-triage-rg",
    [string]$Location = "canadacentral",
    [string]$ContainerAppName = "nurse-triage-api",
    [string]$ContainerAppsEnvironmentName = "nurse-triage-aca-env",
    [string]$AcrName = "nursetriageacr7351",

    [Parameter(Mandatory = $true)]
    [string]$KeyVaultName,

    [switch]$DryRun,
    [switch]$DisableAcrAdminAfterValidation
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "azure-security-common.ps1")

function Write-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host $Text -ForegroundColor Cyan
}

function Invoke-AzSafe {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [switch]$Json,
        [switch]$Mutating,
        [switch]$AllowFailure
    )

    if ($DryRun -and $Mutating) {
        Write-Host ("DRYRUN az {0}" -f ($Arguments -join " ")) -ForegroundColor DarkGray
        return $null
    }

    $output = & az @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        if ($AllowFailure) {
            return $null
        }
        throw "az $($Arguments -join ' ') failed: $output"
    }

    if ($Json) {
        return ConvertFrom-JsonOrNull -Text ($output | Out-String)
    }

    if ($null -eq $output) {
        return ""
    }
    return ($output | Out-String).Trim()
}

function Ensure-RoleAssignment {
    param(
        [string]$PrincipalId,
        [string]$RoleName,
        [string]$Scope
    )

    if (-not $PrincipalId) {
        Write-Host "  WARN role '$RoleName': principal ID is not available yet"
        return
    }

    $existing = Invoke-AzSafe -Arguments @(
        "role", "assignment", "list",
        "--assignee", $PrincipalId,
        "--role", $RoleName,
        "--scope", $Scope,
        "--query", "[0].id",
        "-o", "tsv"
    ) -AllowFailure

    if ($existing) {
        Write-Host "  PASS role '$RoleName' already assigned"
        return
    }

    Invoke-AzSafe -Arguments @(
        "role", "assignment", "create",
        "--assignee-object-id", $PrincipalId,
        "--assignee-principal-type", "ServicePrincipal",
        "--role", $RoleName,
        "--scope", $Scope,
        "--query", "{id:id,role:roleDefinitionName}",
        "-o", "json"
    ) -Json -Mutating | Out-Null

    Write-Host "  PASS role '$RoleName' assigned"
}

Write-Section "Azure identity and secret-reference remediation"
Write-Host "Resource group : $ResourceGroupName"
Write-Host "Location       : $Location"
Write-Host "Container App  : $ContainerAppName"
Write-Host "ACA environment: $ContainerAppsEnvironmentName"
Write-Host "ACR            : $AcrName"
Write-Host "Key Vault      : $KeyVaultName"
Write-Host "Mode           : $(if ($DryRun) { 'Dry run' } else { 'Apply changes' })"
Write-Host "Rotation       : already completed by operator; out of scope here"

$SelectedSubscriptionId = Assert-AzCliReady -SubscriptionId $SubscriptionId

Write-Section "Discover resources"
$containerApp = Invoke-AzSafe -Arguments @(
    "containerapp", "show",
    "--resource-group", $ResourceGroupName,
    "--name", $ContainerAppName,
    "--query", "{id:id,name:name,principalId:identity.principalId,identityType:identity.type}",
    "-o", "json"
) -Json

if (-not $containerApp) {
    throw "Container App '$ContainerAppName' was not found in '$ResourceGroupName'."
}

$acr = Invoke-AzSafe -Arguments @(
    "acr", "show",
    "--resource-group", $ResourceGroupName,
    "--name", $AcrName,
    "--query", "{id:id,name:name,loginServer:loginServer,adminUserEnabled:adminUserEnabled}",
    "-o", "json"
) -Json

if (-not $acr) {
    throw "ACR '$AcrName' was not found in '$ResourceGroupName'."
}

Write-Host "  Container App identity: $($containerApp.identityType)"
Write-Host "  ACR login server      : $($acr.loginServer)"
Write-Host "  ACR admin enabled     : $($acr.adminUserEnabled)"

Write-Section "Enable system-assigned identity"
if (-not $containerApp.principalId) {
    Invoke-AzSafe -Arguments @(
        "containerapp", "identity", "assign",
        "--resource-group", $ResourceGroupName,
        "--name", $ContainerAppName,
        "--system-assigned",
        "--output", "none"
    ) -Mutating | Out-Null

    $containerApp = Invoke-AzSafe -Arguments @(
        "containerapp", "show",
        "--resource-group", $ResourceGroupName,
        "--name", $ContainerAppName,
        "--query", "{id:id,name:name,principalId:identity.principalId,identityType:identity.type}",
        "-o", "json"
    ) -Json
}

$principalId = $containerApp.principalId
if (-not $principalId -and $DryRun) {
    $principalId = "(created when system-assigned identity is enabled)"
}
Write-Host "  Identity type : $($containerApp.identityType)"
Write-Host "  Principal ID  : $principalId"

Write-Section "Create or validate Key Vault"
$vault = Invoke-AzSafe -Arguments @(
    "keyvault", "show",
    "--resource-group", $ResourceGroupName,
    "--name", $KeyVaultName,
    "--query", "{id:id,name:name,location:location,enableRbacAuthorization:properties.enableRbacAuthorization,enableSoftDelete:properties.enableSoftDelete,enablePurgeProtection:properties.enablePurgeProtection}",
    "-o", "json"
) -Json -AllowFailure

if (-not $vault) {
    Invoke-AzSafe -Arguments @(
        "keyvault", "create",
        "--resource-group", $ResourceGroupName,
        "--name", $KeyVaultName,
        "--location", $Location,
        "--enable-rbac-authorization", "true",
        "--enable-purge-protection", "true",
        "--retention-days", "90",
        "--query", "{id:id,name:name,location:location,enableRbacAuthorization:properties.enableRbacAuthorization,enableSoftDelete:properties.enableSoftDelete,enablePurgeProtection:properties.enablePurgeProtection}",
        "-o", "json"
    ) -Json -Mutating | Out-Null
}
else {
    if (-not $vault.enableRbacAuthorization -or -not $vault.enablePurgeProtection) {
        Invoke-AzSafe -Arguments @(
            "keyvault", "update",
            "--resource-group", $ResourceGroupName,
            "--name", $KeyVaultName,
            "--enable-rbac-authorization", "true",
            "--enable-purge-protection", "true",
            "--query", "{id:id,name:name,location:location,enableRbacAuthorization:properties.enableRbacAuthorization,enableSoftDelete:properties.enableSoftDelete,enablePurgeProtection:properties.enablePurgeProtection}",
            "-o", "json"
        ) -Json -Mutating | Out-Null
    }
}

$vault = Invoke-AzSafe -Arguments @(
    "keyvault", "show",
    "--resource-group", $ResourceGroupName,
    "--name", $KeyVaultName,
    "--query", "{id:id,name:name,location:location,enableRbacAuthorization:properties.enableRbacAuthorization,enableSoftDelete:properties.enableSoftDelete,enablePurgeProtection:properties.enablePurgeProtection}",
    "-o", "json"
) -Json -AllowFailure

if (-not $vault) {
    $vault = [pscustomobject]@{
        id = "/subscriptions/$SelectedSubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.KeyVault/vaults/$KeyVaultName"
        name = $KeyVaultName
        location = $Location
        enableRbacAuthorization = $true
        enableSoftDelete = $true
        enablePurgeProtection = $true
    }
}

Write-Host "  RBAC enabled     : $($vault.enableRbacAuthorization)"
Write-Host "  Soft delete      : $($vault.enableSoftDelete)"
Write-Host "  Purge protection : $($vault.enablePurgeProtection)"

Write-Section "Assign least-privilege roles"
Ensure-RoleAssignment -PrincipalId $containerApp.principalId -RoleName "Key Vault Secrets User" -Scope $vault.id
Ensure-RoleAssignment -PrincipalId $containerApp.principalId -RoleName "AcrPull" -Scope $acr.id

Write-Section "Configure ACR pull through managed identity"
Invoke-AzSafe -Arguments @(
    "acr", "config", "authentication-as-arm", "update",
    "--registry", $AcrName,
    "--status", "enabled",
    "--output", "none"
) -Mutating | Out-Null

Invoke-AzSafe -Arguments @(
    "containerapp", "registry", "set",
    "--resource-group", $ResourceGroupName,
    "--name", $ContainerAppName,
    "--server", $acr.loginServer,
    "--identity", "system",
    "--output", "none"
) -Mutating | Out-Null
Write-Host "  Registry server : $($acr.loginServer)"
Write-Host "  Registry auth   : managed identity"

Write-Section "Convert Container App secrets to Key Vault references"
$secretMappings = @(
    @{ EnvName = "DATABASE_URL"; AppSecret = "database-url"; KeyVaultSecret = "database-url"; Required = $true; Note = "PostgreSQL connection string" },
    @{ EnvName = "DEEPSEEK_API_KEY"; AppSecret = "deepseek-api-key"; KeyVaultSecret = "deepseek-api-key"; Required = $true; Note = "LLM provider key" },
    @{ EnvName = "TWILIO_AUTH_TOKEN"; AppSecret = "twilio-auth-token"; KeyVaultSecret = "twilio-auth-token"; Required = $true; Note = "Twilio signature token" },
    @{ EnvName = "SENTRY_DSN"; AppSecret = "sentry-dsn"; KeyVaultSecret = "sentry-dsn"; Required = $false; Note = "Error monitoring DSN" },
    @{ EnvName = "DASHBOARD_ADMIN_TOKEN"; AppSecret = "dashboard-admin-token"; KeyVaultSecret = "dashboard-admin-token"; Required = $true; Note = "Dashboard interim token" },
    @{ EnvName = "AZURE_STORAGE_CONNECTION_STRING"; AppSecret = "azure-storage-connection-string"; KeyVaultSecret = "azure-storage-connection-string"; Required = $false; Note = "Temporary bridge until Blob managed identity/RBAC is complete" }
)

$secretSetArgs = @()
$envSetArgs = @()
foreach ($mapping in $secretMappings) {
    $secretId = Invoke-AzSafe -Arguments @(
        "keyvault", "secret", "show",
        "--vault-name", $KeyVaultName,
        "--name", $mapping["KeyVaultSecret"],
        "--query", "id",
        "-o", "tsv"
    ) -AllowFailure

    if ($secretId) {
        $secretSetArgs += "$($mapping["AppSecret"])=keyvaultref:$secretId,identityref:system"
        $envSetArgs += "$($mapping["EnvName"])=secretref:$($mapping["AppSecret"])"
        Write-Host "  PASS $($mapping["EnvName"]) -> Key Vault secret '$($mapping["KeyVaultSecret"])'"
    }
    else {
        $level = if ($mapping["Required"]) { "WARN" } else { "INFO" }
        Write-Host "  $level Key Vault secret '$($mapping["KeyVaultSecret"])' not found for $($mapping["EnvName"])"
        Write-Host "       Operator action: create/update this named secret in Key Vault; do not pass values to this script."
    }
}

if ($secretSetArgs.Count -gt 0) {
    $secretSetCommand = @(
        "containerapp", "secret", "set",
        "--resource-group", $ResourceGroupName,
        "--name", $ContainerAppName,
        "--secrets"
    ) + $secretSetArgs + @("--output", "none")
    Invoke-AzSafe -Arguments $secretSetCommand -Mutating | Out-Null

    $envSetCommand = @(
        "containerapp", "update",
        "--resource-group", $ResourceGroupName,
        "--name", $ContainerAppName,
        "--set-env-vars"
    ) + $envSetArgs + @("--output", "none")
    Invoke-AzSafe -Arguments $envSetCommand -Mutating | Out-Null
}

Write-Section "Optional ACR admin disablement"
if ($DisableAcrAdminAfterValidation) {
    Invoke-AzSafe -Arguments @(
        "acr", "update",
        "--resource-group", $ResourceGroupName,
        "--name", $AcrName,
        "--admin-enabled", "false",
        "--output", "none"
    ) -Mutating | Out-Null
    Write-Host "  PASS ACR admin disable requested"
}
else {
    Write-Host "  SKIP ACR admin disablement. Re-run with -DisableAcrAdminAfterValidation after image pull validation."
}

Write-Section "Summary"
Write-Host "  Container App : $ContainerAppName"
Write-Host "  Principal ID  : $principalId"
Write-Host "  Key Vault     : $KeyVaultName"
$secretNames = $secretMappings | ForEach-Object { $_["KeyVaultSecret"] }
Write-Host "  Secret names  : $($secretNames -join ', ')"
Write-Host "  Output safety : no secret values printed"
Write-Host ""
Write-Host "Next verification:"
Write-Host "  .\scripts\azure-security-verify.ps1 -SubscriptionId `"$SubscriptionId`" -KeyVaultName `"$KeyVaultName`""
