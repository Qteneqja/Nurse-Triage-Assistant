# Azure Security Hardening

This document tracks repo-side remediation for the Nurse Triage Assistant
production deployment on Azure Container Apps in Canada Central.

## Scope Boundary

Production credential rotation has already been completed by the operator. Do
not treat rotation as part of this remediation. The repo changes here prevent
future exposure by moving runtime configuration toward managed identity,
Container App secret references, and Key Vault references.

Rotate or revoke credentials again only for a future incident, such as confirmed
secret disclosure, suspicious provider activity, unexpected Key Vault reads,
unauthorized Container App config access, suspicious ACR pulls, database access
anomalies, or storage exfiltration indicators.

## Verification After Remediation

Run the safe read-only verifier. It uses `az --query` projections and never
prints secret values.

```powershell
.\scripts\azure-security-verify.ps1 `
  -SubscriptionId "<subscription-id>" `
  -ResourceGroupName "nurse-triage-rg" `
  -KeyVaultName "<production-key-vault-name>" `
  -JsonOut ".\reports\azure-security-evidence.json"
```

Expected high-level result:

- Container App identity is enabled.
- Registry configuration uses managed identity, not username/password.
- Sensitive environment variables use `secretRef`; none have plain `value`.
- Container App secrets are backed by Key Vault references where applicable.
- ACR admin user is disabled after image pull validation.
- Key Vault RBAC, soft delete, and purge protection are enabled.
- PostgreSQL and Storage network gaps are either closed or documented as open
  platform work.

## Managed Identity And Key Vault Runbook

Use the safe remediation script. It does not accept secret values and does not
rotate credentials.

```powershell
.\scripts\azure-remediate-identity-secrets.ps1 `
  -SubscriptionId "<subscription-id>" `
  -ResourceGroupName "nurse-triage-rg" `
  -Location "canadacentral" `
  -ContainerAppName "nurse-triage-api" `
  -ContainerAppsEnvironmentName "nurse-triage-aca-env" `
  -AcrName "nursetriageacr7351" `
  -KeyVaultName "<production-key-vault-name>" `
  -DryRun
```

Then run without `-DryRun` after review.

The script expects the operator to create or maintain these Key Vault secret
names. Do not pass values to the script.

| App env var | Container App secret | Key Vault secret |
|---|---|---|
| `DATABASE_URL` | `database-url` | `database-url` |
| `DEEPSEEK_API_KEY` | `deepseek-api-key` | `deepseek-api-key` |
| `TWILIO_AUTH_TOKEN` | `twilio-auth-token` | `twilio-auth-token` |
| `SENTRY_DSN` | `sentry-dsn` | `sentry-dsn` |
| `DASHBOARD_ADMIN_TOKEN` | `dashboard-admin-token` | `dashboard-admin-token` |
| `AZURE_STORAGE_CONNECTION_STRING` | `azure-storage-connection-string` | `azure-storage-connection-string` |

`AZURE_STORAGE_CONNECTION_STRING` is a temporary bridge only. The target state is
managed identity plus Azure RBAC for Blob access.

## ACR Admin Disablement

Target state:

- Container App has system-assigned managed identity.
- The identity has `AcrPull` at the ACR scope.
- `az containerapp registry set` uses `--identity system`.
- A new revision has successfully pulled the image.
- ACR admin user is disabled.

After validation:

```powershell
.\scripts\azure-remediate-identity-secrets.ps1 `
  -SubscriptionId "<subscription-id>" `
  -KeyVaultName "<production-key-vault-name>" `
  -DisableAcrAdminAfterValidation
```

Verify:

```powershell
az acr show -g nurse-triage-rg -n nursetriageacr7351 `
  --query "{adminUserEnabled:adminUserEnabled,publicNetworkAccess:publicNetworkAccess,sku:sku.name}"
```

## Storage Managed Identity Plan

Current bridge support may still use a storage connection string. Target state:

1. Add Azure SDK support for `DefaultAzureCredential` and Blob service endpoint.
2. Assign the Container App identity `Storage Blob Data Contributor` or a
   narrower custom role scoped to the report container.
3. Deploy with both managed identity Blob path and current secret bridge.
4. Validate report upload and restore/read workflows.
5. Remove `AZURE_STORAGE_CONNECTION_STRING`.
6. Disable Shared Key access.
7. Set network default action to `Deny` and add private endpoint/private DNS.
8. Enable blob soft delete, container soft delete, and versioning according to
   the approved retention schedule.

## PostgreSQL Private Networking Plan

Target state:

1. Design Container Apps environment VNet integration.
2. Add PostgreSQL private endpoint or private access with private DNS.
3. Validate `/ready` from Container Apps through the private path.
4. Disable PostgreSQL public network access.
5. Review backup retention, HA, geo-backup, and storage autogrow against RTO/RPO.
6. Evaluate Microsoft Entra authentication for PostgreSQL as a later password
   reduction step.

## Defender And Azure Policy Checklist

Defender for Cloud:

- Register `Microsoft.Security`.
- Enable Defender CSPM and Defender for Containers as approved by budget.
- Review container image and registry recommendations weekly.
- Route Defender alerts to Log Analytics or Sentinel if available.

Azure Policy:

- ACR admin account disabled.
- Key Vault purge protection enabled.
- Storage Shared Key disabled.
- Storage public network access restricted.
- PostgreSQL public network access disabled.
- Diagnostic settings required for production resources.
- Allowed locations restricted to Canada Central and approved regions.
- Required tags: `owner`, `environment`, `data-classification`, `cost-center`.

## Diagnostic Settings Checklist

Send diagnostics to the production Log Analytics workspace for:

- Azure Activity Log.
- Container App.
- Container Apps environment.
- Azure Container Registry.
- PostgreSQL Flexible Server.
- Storage account and Blob service.
- Key Vault.

Verification:

```powershell
.\scripts\azure-security-verify.ps1 `
  -SubscriptionId "<subscription-id>" `
  -KeyVaultName "<production-key-vault-name>"
```

## Application Controls

Security headers are applied by FastAPI middleware:

- HSTS.
- `X-Content-Type-Options: nosniff`.
- `Referrer-Policy: no-referrer`.
- CSP with default `frame-ancestors 'none'`.
- `X-Frame-Options: DENY`.
- restrictive `Permissions-Policy`.
- `Cache-Control: no-store` for sensitive API and dashboard responses.

CSP can be adjusted with:

- `SECURITY_CSP`
- `SECURITY_FRAME_ANCESTORS`

Dashboard production posture:

- Preferred: Entra-authenticated front door or private admin surface.
- Interim: token auth with protected shell and API routes.
- Fallback: `DASHBOARD_ENABLED=false`.

Production startup validation fails if `DASHBOARD_ENABLED=true` without a strong
`DASHBOARD_ADMIN_TOKEN`.

## Local Security Checks

```powershell
pre-commit run --all-files
python -m pytest tests/test_security_headers.py tests/test_phase12_dashboard.py
bandit -r src -c pyproject.toml --severity-level high --confidence-level high
pip-audit -r requirements.txt --progress-spinner off
```

CI keeps Gitleaks, runs Bandit as an enforcing high/high gate, and uses
`pip-audit` for Python dependency vulnerabilities.

## Evidence Pack Checklist

Maintain these artifacts for governance and pilot review:

- Architecture diagram.
- Data flow and trust boundaries.
- Azure asset inventory.
- Risk register and accepted exceptions.
- Access review evidence.
- Security test results.
- Incident runbooks.
- Backup and restore test.
- Azure Policy assignment export.
- Defender for Cloud recommendation export.
- Sanitized output from `azure-security-verify.ps1`.

Never include real secret values in evidence.
