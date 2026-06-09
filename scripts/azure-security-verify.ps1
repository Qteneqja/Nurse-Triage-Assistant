<#
.SYNOPSIS
    Read-only Azure security posture verification for Nurse Triage Assistant.

.DESCRIPTION
    Checks Container App identity/secret posture, ACR admin status, Key Vault
    controls, PostgreSQL and Storage network posture, Defender registration,
    Azure Policy assignments, and diagnostic settings. Output is sanitized and
    never includes secret values.

.PARAMETER SubscriptionId
    Azure subscription ID to inspect.

.PARAMETER ResourceGroupName
    Resource group containing production resources.

.PARAMETER ContainerAppName
    Container App name.

.PARAMETER ContainerAppsEnvironmentName
    Container Apps environment name.

.PARAMETER AcrName
    Azure Container Registry name.

.PARAMETER KeyVaultName
    Key Vault name to verify.

.PARAMETER PostgreSqlServerName
    Optional PostgreSQL Flexible Server name. If omitted, the first server in
    the resource group is inspected.

.PARAMETER StorageAccountName
    Optional Storage account name. If omitted, the first StorageV2 account in
    the resource group is inspected.

.PARAMETER Strict
    Exit non-zero for WARN and P1/P2 FAIL findings, not only P0 failures.

.PARAMETER JsonOut
    Optional path for a sanitized JSON evidence export.

.EXAMPLE
    .\scripts\azure-security-verify.ps1 `
        -SubscriptionId "00000000-0000-0000-0000-000000000000" `
        -KeyVaultName "kv-nurse-triage-prod" `
        -JsonOut ".\reports\azure-security-evidence.json"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [string]$ResourceGroupName = "nurse-triage-rg",
    [string]$ContainerAppName = "nurse-triage-api",
    [string]$ContainerAppsEnvironmentName = "nurse-triage-aca-env",
    [string]$AcrName = "nursetriageacr7351",
    [string]$KeyVaultName = "",
    [string]$PostgreSqlServerName = "",
    [string]$StorageAccountName = "",
    [switch]$Strict,
    [string]$JsonOut = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "azure-security-common.ps1")

$Checks = New-Object System.Collections.Generic.List[object]
$Evidence = [ordered]@{
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    subscriptionId = $SubscriptionId
    resourceGroup = $ResourceGroupName
    resources = [ordered]@{}
    checks = @()
}

function Invoke-AzQuery {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [switch]$Json,
        [switch]$Required
    )

    $maxAttempts = 3
    $exitCode = 1
    $output = @()

    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $rawOutput = & az @Arguments 2>&1
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }

        $output = @(
            $rawOutput | ForEach-Object { $_.ToString() } |
                Where-Object { $_ -notmatch "^WARNING:" }
        )

        if ($exitCode -eq 0) {
            break
        }

        if ($attempt -lt $maxAttempts) {
            Start-Sleep -Seconds (2 * $attempt)
        }
    }

    if ($exitCode -ne 0) {
        if ($Required) {
            throw "az $($Arguments -join ' ') failed after $maxAttempts attempts."
        }
        return $null
    }

    if ($Json) {
        return ConvertFrom-JsonOrNull -Text ($output | Out-String)
    }

    return ($output | Out-String).Trim()
}

function Add-Check {
    param(
        [ValidateSet("PASS", "WARN", "FAIL")]
        [string]$Status,

        [ValidateSet("P0", "P1", "P2", "INFO")]
        [string]$Severity,

        [string]$Area,
        [string]$Message,
        [hashtable]$Data = @{}
    )

    $item = [pscustomobject]@{
        status = $Status
        severity = $Severity
        area = $Area
        message = $Message
        data = $Data
    }
    $Checks.Add($item) | Out-Null

    $color = switch ($Status) {
        "PASS" { "Green" }
        "WARN" { "Yellow" }
        default { "Red" }
    }
    Write-Host ("[{0}] {1} {2}: {3}" -f $Status, $Severity, $Area, $Message) -ForegroundColor $color
}

function Test-Diagnostics {
    param(
        [string]$Area,
        [string]$ResourceId
    )

    if (-not $ResourceId) {
        Add-Check -Status "WARN" -Severity "P1" -Area $Area -Message "Diagnostic settings skipped because resource ID is unavailable"
        return
    }

    $settings = Invoke-AzQuery -Arguments @(
        "monitor", "diagnostic-settings", "list",
        "--resource", $ResourceId,
        "--query", "[].{name:name,workspaceId:workspaceId}",
        "-o", "json"
    ) -Json

    $count = @($settings).Count
    if ($count -gt 0) {
        Add-Check -Status "PASS" -Severity "P1" -Area $Area -Message "Diagnostic settings present" -Data @{ count = $count }
    }
    else {
        Add-Check -Status "WARN" -Severity "P1" -Area $Area -Message "No diagnostic settings detected"
    }
}

Assert-AzCliReady -SubscriptionId $SubscriptionId | Out-Null

Write-Host ""
Write-Host "Azure Security Posture Verification" -ForegroundColor Cyan
Write-Host "Subscription : $SubscriptionId"
Write-Host "ResourceGroup: $ResourceGroupName"
Write-Host "Strict       : $($Strict.IsPresent)"
Write-Host ""

$SensitiveEnv = @(
    "DATABASE_URL",
    "DEEPSEEK_API_KEY",
    "TWILIO_AUTH_TOKEN",
    "SENTRY_DSN",
    "DASHBOARD_ADMIN_TOKEN",
    "AZURE_STORAGE_CONNECTION_STRING"
)
$RequiredSensitiveEnv = @(
    "DATABASE_URL",
    "DEEPSEEK_API_KEY",
    "TWILIO_AUTH_TOKEN",
    "DASHBOARD_ADMIN_TOKEN"
)

# Container App
$app = Invoke-AzQuery -Arguments @(
    "containerapp", "show",
    "--resource-group", $ResourceGroupName,
    "--name", $ContainerAppName,
    "--query", "{id:id,name:name,identityType:identity.type,principalId:identity.principalId,registries:properties.configuration.registries[].{server:server,identity:identity,username:username,passwordSecretRef:passwordSecretRef},env:properties.template.containers[0].env[].{name:name,secretRef:secretRef,hasPlainValue:contains(keys(@), 'value')},secrets:properties.configuration.secrets[].{name:name,keyVaultUrl:keyVaultUrl,identity:identity}}",
    "-o", "json"
) -Json

if (-not $app) {
    Add-Check -Status "FAIL" -Severity "P0" -Area "ContainerApp" -Message "Container App not found"
}
else {
    $Evidence["resources"]["containerApp"] = @{
        name = $ContainerAppName
        id = $app.id
        identityType = $app.identityType
        principalId = $app.principalId
    }

    if ($app.principalId) {
        Add-Check -Status "PASS" -Severity "P0" -Area "ContainerApp" -Message "System-assigned managed identity is enabled" -Data @{ principalId = $app.principalId }
    }
    else {
        Add-Check -Status "FAIL" -Severity "P0" -Area "ContainerApp" -Message "Managed identity is not enabled"
    }

    $registries = @($app.registries)
    $identityRegistries = $registries | Where-Object { $_.identity }
    $credentialRegistries = $registries | Where-Object { $_.username -or $_.passwordSecretRef }
    if (@($credentialRegistries).Count -gt 0) {
        Add-Check -Status "FAIL" -Severity "P0" -Area "ContainerApp" -Message "Registry configuration still references username/password material"
    }
    elseif (@($identityRegistries).Count -gt 0) {
        Add-Check -Status "PASS" -Severity "P0" -Area "ContainerApp" -Message "Registry uses managed identity"
    }
    else {
        Add-Check -Status "WARN" -Severity "P1" -Area "ContainerApp" -Message "No managed-identity registry configuration detected"
    }

    $envRows = @($app.env)
    foreach ($name in $SensitiveEnv) {
        $row = $envRows | Where-Object { $_.name -eq $name } | Select-Object -First 1
        if (-not $row) {
            $severity = if ($name -in $RequiredSensitiveEnv) { "P0" } else { "P2" }
            $status = if ($name -in $RequiredSensitiveEnv) { "FAIL" } else { "WARN" }
            Add-Check -Status $status -Severity $severity -Area "ContainerApp" -Message "$name is not configured"
            continue
        }

        if ($row.hasPlainValue) {
            Add-Check -Status "FAIL" -Severity "P0" -Area "ContainerApp" -Message "$name has a plain environment value"
        }
        elseif ($row.secretRef) {
            Add-Check -Status "PASS" -Severity "P0" -Area "ContainerApp" -Message "$name uses secretRef '$($row.secretRef)'"
        }
        else {
            Add-Check -Status "FAIL" -Severity "P0" -Area "ContainerApp" -Message "$name is configured without a secret reference"
        }
    }

    $kvSecretRefs = @($app.secrets) | Where-Object { $_.keyVaultUrl }
    if (@($kvSecretRefs).Count -gt 0) {
        Add-Check -Status "PASS" -Severity "P0" -Area "ContainerApp" -Message "Container App secrets include Key Vault references" -Data @{ count = @($kvSecretRefs).Count }
    }
    else {
        Add-Check -Status "WARN" -Severity "P1" -Area "ContainerApp" -Message "No Key Vault-backed Container App secrets detected"
    }

    Test-Diagnostics -Area "ContainerApp" -ResourceId $app.id
}

# Container Apps environment diagnostics
$acaEnv = Invoke-AzQuery -Arguments @(
    "containerapp", "env", "show",
    "--resource-group", $ResourceGroupName,
    "--name", $ContainerAppsEnvironmentName,
    "--query", "{id:id,name:name,appLogsDestination:properties.appLogsConfiguration.destination,zoneRedundant:properties.zoneRedundant}",
    "-o", "json"
) -Json
if ($acaEnv) {
    $Evidence["resources"]["containerAppsEnvironment"] = @{
        name = $ContainerAppsEnvironmentName
        id = $acaEnv.id
        appLogsDestination = $acaEnv.appLogsDestination
    }
    if ($acaEnv.appLogsDestination) {
        Add-Check -Status "PASS" -Severity "P1" -Area "ContainerAppsEnv" -Message "App logs destination reported as '$($acaEnv.appLogsDestination)'"
    }
    else {
        Add-Check -Status "WARN" -Severity "P1" -Area "ContainerAppsEnv" -Message "App logs destination not reported"
    }
    Test-Diagnostics -Area "ContainerAppsEnv" -ResourceId $acaEnv.id
}
else {
    Add-Check -Status "WARN" -Severity "P1" -Area "ContainerAppsEnv" -Message "Container Apps environment not found"
}

# ACR
$acr = Invoke-AzQuery -Arguments @(
    "acr", "show",
    "--resource-group", $ResourceGroupName,
    "--name", $AcrName,
    "--query", "{id:id,name:name,sku:sku.name,adminUserEnabled:adminUserEnabled,publicNetworkAccess:publicNetworkAccess}",
    "-o", "json"
) -Json
if ($acr) {
    $Evidence["resources"]["acr"] = @{
        name = $AcrName
        id = $acr.id
        sku = $acr.sku
        publicNetworkAccess = $acr.publicNetworkAccess
    }
    if ($acr.adminUserEnabled -eq $false) {
        Add-Check -Status "PASS" -Severity "P0" -Area "ACR" -Message "ACR admin user is disabled"
    }
    else {
        Add-Check -Status "FAIL" -Severity "P0" -Area "ACR" -Message "ACR admin user is enabled"
    }
    Add-Check -Status "PASS" -Severity "INFO" -Area "ACR" -Message "ACR SKU is '$($acr.sku)'"
    if ($acr.publicNetworkAccess -eq "Disabled") {
        Add-Check -Status "PASS" -Severity "P1" -Area "ACR" -Message "Public network access is disabled"
    }
    else {
        Add-Check -Status "WARN" -Severity "P1" -Area "ACR" -Message "Public network access is '$($acr.publicNetworkAccess)'"
    }
    Test-Diagnostics -Area "ACR" -ResourceId $acr.id
}
else {
    Add-Check -Status "FAIL" -Severity "P0" -Area "ACR" -Message "ACR not found"
}

# Key Vault
if ($KeyVaultName) {
    $kv = Invoke-AzQuery -Arguments @(
        "keyvault", "show",
        "--resource-group", $ResourceGroupName,
        "--name", $KeyVaultName,
        "--query", "{id:id,name:name,enableRbacAuthorization:properties.enableRbacAuthorization,enableSoftDelete:properties.enableSoftDelete,enablePurgeProtection:properties.enablePurgeProtection,publicNetworkAccess:properties.publicNetworkAccess}",
        "-o", "json"
    ) -Json

    if ($kv) {
        $Evidence["resources"]["keyVault"] = @{
            name = $KeyVaultName
            id = $kv.id
            publicNetworkAccess = $kv.publicNetworkAccess
        }
        if ($kv.enableRbacAuthorization) {
            Add-Check -Status "PASS" -Severity "P0" -Area "KeyVault" -Message "RBAC permission model is enabled"
        }
        else {
            Add-Check -Status "FAIL" -Severity "P0" -Area "KeyVault" -Message "RBAC permission model is not enabled"
        }
        if ($kv.enableSoftDelete) {
            Add-Check -Status "PASS" -Severity "P0" -Area "KeyVault" -Message "Soft delete is enabled"
        }
        else {
            Add-Check -Status "FAIL" -Severity "P0" -Area "KeyVault" -Message "Soft delete is not enabled"
        }
        if ($kv.enablePurgeProtection) {
            Add-Check -Status "PASS" -Severity "P0" -Area "KeyVault" -Message "Purge protection is enabled"
        }
        else {
            Add-Check -Status "FAIL" -Severity "P0" -Area "KeyVault" -Message "Purge protection is not enabled"
        }
        Test-Diagnostics -Area "KeyVault" -ResourceId $kv.id
    }
    else {
        Add-Check -Status "FAIL" -Severity "P0" -Area "KeyVault" -Message "Key Vault '$KeyVaultName' not found"
    }
}
else {
    Add-Check -Status "WARN" -Severity "P1" -Area "KeyVault" -Message "KeyVaultName was not provided"
}

# PostgreSQL
if (-not $PostgreSqlServerName) {
    $PostgreSqlServerName = Invoke-AzQuery -Arguments @(
        "postgres", "flexible-server", "list",
        "--resource-group", $ResourceGroupName,
        "--query", "[0].name",
        "-o", "tsv"
    )
}

if ($PostgreSqlServerName) {
    $pg = Invoke-AzQuery -Arguments @(
        "postgres", "flexible-server", "show",
        "--resource-group", $ResourceGroupName,
        "--name", $PostgreSqlServerName,
        "--query", "{id:id,name:name,version:version,publicNetworkAccess:network.publicNetworkAccess,backupRetentionDays:backup.backupRetentionDays,geoRedundantBackup:backup.geoRedundantBackup,highAvailability:highAvailability.mode,state:state}",
        "-o", "json"
    ) -Json

    if ($pg) {
        $Evidence["resources"]["postgresql"] = @{
            name = $PostgreSqlServerName
            id = $pg.id
            version = $pg.version
        }
        if ($pg.publicNetworkAccess -eq "Disabled") {
            Add-Check -Status "PASS" -Severity "P0" -Area "PostgreSQL" -Message "Public network access is disabled"
        }
        else {
            Add-Check -Status "FAIL" -Severity "P0" -Area "PostgreSQL" -Message "Public network access is '$($pg.publicNetworkAccess)'"
        }
        if ([int]$pg.backupRetentionDays -ge 14) {
            Add-Check -Status "PASS" -Severity "P1" -Area "PostgreSQL" -Message "Backup retention is $($pg.backupRetentionDays) days"
        }
        else {
            Add-Check -Status "WARN" -Severity "P1" -Area "PostgreSQL" -Message "Backup retention is $($pg.backupRetentionDays) days"
        }
        Add-Check -Status "WARN" -Severity "P1" -Area "PostgreSQL" -Message "HA mode reported as '$($pg.highAvailability)'"
        Add-Check -Status "WARN" -Severity "P1" -Area "PostgreSQL" -Message "Geo-backup reported as '$($pg.geoRedundantBackup)'"

        $pgPrivateEndpoints = Invoke-AzQuery -Arguments @(
            "network", "private-endpoint-connection", "list",
            "--id", $pg.id,
            "--query", "[].{name:name,status:properties.privateLinkServiceConnectionState.status}",
            "-o", "json"
        ) -Json
        if (@($pgPrivateEndpoints).Count -gt 0) {
            Add-Check -Status "PASS" -Severity "P0" -Area "PostgreSQL" -Message "Private endpoint connections detected" -Data @{ count = @($pgPrivateEndpoints).Count }
        }
        else {
            Add-Check -Status "FAIL" -Severity "P0" -Area "PostgreSQL" -Message "No private endpoint/private access detected"
        }
        Test-Diagnostics -Area "PostgreSQL" -ResourceId $pg.id
    }
}
else {
    Add-Check -Status "WARN" -Severity "P1" -Area "PostgreSQL" -Message "PostgreSQL server not found"
}

# Storage
if (-not $StorageAccountName) {
    $StorageAccountName = Invoke-AzQuery -Arguments @(
        "storage", "account", "list",
        "--resource-group", $ResourceGroupName,
        "--query", "[?kind=='StorageV2'] | [0].name",
        "-o", "tsv"
    )
}

if ($StorageAccountName) {
    $storage = Invoke-AzQuery -Arguments @(
        "storage", "account", "show",
        "--resource-group", $ResourceGroupName,
        "--name", $StorageAccountName,
        "--query", "{id:id,name:name,allowSharedKeyAccess:allowSharedKeyAccess,publicNetworkAccess:publicNetworkAccess,networkDefaultAction:networkRuleSet.defaultAction,allowBlobPublicAccess:allowBlobPublicAccess,minimumTlsVersion:minimumTlsVersion}",
        "-o", "json"
    ) -Json

    if ($storage) {
        $Evidence["resources"]["storage"] = @{
            name = $StorageAccountName
            id = $storage.id
            publicNetworkAccess = $storage.publicNetworkAccess
        }
        if ($storage.allowSharedKeyAccess -eq $false) {
            Add-Check -Status "PASS" -Severity "P0" -Area "Storage" -Message "Shared Key access is disabled"
        }
        else {
            Add-Check -Status "FAIL" -Severity "P0" -Area "Storage" -Message "Shared Key access is allowed or unset"
        }
        if ($storage.networkDefaultAction -eq "Deny") {
            Add-Check -Status "PASS" -Severity "P0" -Area "Storage" -Message "Network default action is Deny"
        }
        else {
            Add-Check -Status "FAIL" -Severity "P0" -Area "Storage" -Message "Network default action is '$($storage.networkDefaultAction)'"
        }
        if ($storage.allowBlobPublicAccess -eq $false) {
            Add-Check -Status "PASS" -Severity "P1" -Area "Storage" -Message "Public blob access is disabled"
        }
        else {
            Add-Check -Status "FAIL" -Severity "P1" -Area "Storage" -Message "Public blob access is not disabled"
        }

        $blobProps = Invoke-AzQuery -Arguments @(
            "storage", "blob", "service-properties", "show",
            "--account-name", $StorageAccountName,
            "--auth-mode", "login",
            "--query", "{deleteRetentionEnabled:deleteRetentionPolicy.enabled,deleteRetentionDays:deleteRetentionPolicy.days,containerDeleteRetentionEnabled:containerDeleteRetentionPolicy.enabled,isVersioningEnabled:isVersioningEnabled}",
            "-o", "json"
        ) -Json
        if ($blobProps) {
            Add-Check -Status "PASS" -Severity "P1" -Area "Storage" -Message "Blob retention/versioning status read" -Data @{
                deleteRetentionEnabled = $blobProps.deleteRetentionEnabled
                isVersioningEnabled = $blobProps.isVersioningEnabled
            }
        }
        else {
            Add-Check -Status "WARN" -Severity "P1" -Area "Storage" -Message "Could not read blob retention/versioning status"
        }
        Test-Diagnostics -Area "Storage" -ResourceId $storage.id
    }
}
else {
    Add-Check -Status "WARN" -Severity "P1" -Area "Storage" -Message "Storage account not found"
}

# Defender
$securityRegistration = Invoke-AzQuery -Arguments @(
    "provider", "show",
    "--namespace", "Microsoft.Security",
    "--query", "registrationState",
    "-o", "tsv"
)
if ($securityRegistration -eq "Registered") {
    Add-Check -Status "PASS" -Severity "P1" -Area "Defender" -Message "Microsoft.Security provider is registered"
}
else {
    Add-Check -Status "FAIL" -Severity "P1" -Area "Defender" -Message "Microsoft.Security provider registration is '$securityRegistration'"
}

$pricing = Invoke-AzQuery -Arguments @(
    "security", "pricing", "list",
    "--query", "value[].{name:name,pricingTier:pricingTier,subPlan:subPlan}",
    "-o", "json"
) -Json
if ($pricing) {
    $enabledPlans = @($pricing) | Where-Object { $_.pricingTier -eq "Standard" }
    if (@($enabledPlans).Count -gt 0) {
        Add-Check -Status "PASS" -Severity "P1" -Area "Defender" -Message "Defender pricing plans are enabled" -Data @{ enabledPlanCount = @($enabledPlans).Count }
    }
    else {
        Add-Check -Status "WARN" -Severity "P1" -Area "Defender" -Message "No Defender Standard pricing plans detected"
    }
}
else {
    Add-Check -Status "WARN" -Severity "P1" -Area "Defender" -Message "Could not read Defender pricing plans"
}

# Azure Policy
$subscriptionScope = "/subscriptions/$SubscriptionId"
$rgScope = "$subscriptionScope/resourceGroups/$ResourceGroupName"
$subPolicies = Invoke-AzQuery -Arguments @(
    "policy", "assignment", "list",
    "--scope", $subscriptionScope,
    "--query", "[].{name:name,displayName:displayName,scope:scope,enforcementMode:enforcementMode}",
    "-o", "json"
) -Json
$rgPolicies = Invoke-AzQuery -Arguments @(
    "policy", "assignment", "list",
    "--scope", $rgScope,
    "--query", "[].{name:name,displayName:displayName,scope:scope,enforcementMode:enforcementMode}",
    "-o", "json"
) -Json
$policyCount = @($subPolicies).Count + @($rgPolicies).Count
if ($policyCount -gt 0) {
    Add-Check -Status "PASS" -Severity "P1" -Area "AzurePolicy" -Message "Policy assignments detected" -Data @{ count = $policyCount }
}
else {
    Add-Check -Status "FAIL" -Severity "P1" -Area "AzurePolicy" -Message "No policy assignments detected at subscription or resource-group scope"
}

Write-Host ""
Write-Host "Summary" -ForegroundColor Cyan
$grouped = $Checks | Group-Object status
foreach ($group in $grouped) {
    Write-Host ("  {0}: {1}" -f $group.Name, $group.Count)
}

if ($JsonOut) {
    $outDir = Split-Path -Parent $JsonOut
    if ($outDir -and -not (Test-Path $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }
    $evidenceExport = [ordered]@{
        generatedAtUtc = $Evidence["generatedAtUtc"]
        subscriptionId = $Evidence["subscriptionId"]
        resourceGroup = $Evidence["resourceGroup"]
        resources = $Evidence["resources"]
        checks = @(
            $Checks | ForEach-Object {
                [ordered]@{
                    status = $_.status
                    severity = $_.severity
                    area = $_.area
                    message = $_.message
                    data = $_.data
                }
            }
        )
    }
    $evidenceExport | ConvertTo-Json -Depth 10 | Set-Content -Path $JsonOut -Encoding UTF8
    Write-Host "Sanitized evidence written to: $JsonOut"
}

$p0Failures = @($Checks | Where-Object { $_.status -eq "FAIL" -and $_.severity -eq "P0" })
$strictIssues = @($Checks | Where-Object { $_.status -ne "PASS" })

if ($p0Failures.Count -gt 0) {
    exit 1
}

if ($Strict -and $strictIssues.Count -gt 0) {
    exit 2
}

exit 0
