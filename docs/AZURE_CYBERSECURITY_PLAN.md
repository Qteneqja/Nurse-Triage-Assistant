# Azure Cybersecurity Requirements And Remediation Plan

Date: 2026-05-18

Scope: Nurse Triage Assistant production deployment in Azure Canada Central, local repository security controls, and live Azure resource configuration visible through read-only Azure CLI checks.

Important handling note: The live Container App previously exposed several credentials as plain environment variable values in Azure control-plane output. This plan intentionally does not reproduce those values. Production credential rotation has already been completed by the operator; do not treat rotation as part of the remaining repo-side remediation unless a future compromise is discovered.

## Executive Summary

The application has a solid baseline at the code level: production config validation, HTTPS endpoint verification, Twilio webhook signature enforcement, request correlation IDs, rate limiting, PHI masking, Sentry PHI scrubbing, CI secret/security scanning, non-root container runtime, PostgreSQL persistence, and Log Analytics app log forwarding through the Container Apps environment.

The largest remaining gaps are Azure platform hardening and governance:

1. P0 - Secrets are not fully protected against future control-plane exposure. Several production credentials were previously plain Container App environment values instead of secret references or Key Vault references. Rotation is complete; move all secrets to Key Vault via managed identity to prevent recurrence.
2. P0 - Managed identity is not enabled on the Container App. The app pulls from ACR using registry credentials and uses connection strings/passwords for Azure dependencies.
3. P0 - ACR admin user is enabled. This is a high-privilege shared credential and should be replaced with managed identity plus AcrPull.
4. P0 - Database and storage networking are public. PostgreSQL public network access is enabled and has no private endpoint; storage default network action is Allow.
5. P1 - Defender for Cloud is not active. Microsoft.Security is not registered, so Defender posture, regulatory compliance, and container/image security recommendations are not running.
6. P1 - No Azure Policy assignments are present at the subscription scope. Security requirements are documented but not enforced as guardrails.
7. P1 - Diagnostic settings need to be completed and verified for every resource. Container Apps environment logs go to Log Analytics, but resource-level diagnostic coverage for PostgreSQL, ACR, Storage, and Key Vault is not confirmed.
8. P1 - App security headers are incomplete. Live `/health` response has `X-Request-ID`, but no HSTS, X-Content-Type-Options, frame, referrer, or CSP headers.
9. P1 - Dashboard shell is publicly reachable. The API is blocked without admin token, but the shell route returns HTML. Either require auth at the shell/reverse-proxy layer or disable the dashboard in production until an auth front door is in place.

## Evidence Snapshot

### Live Azure Resources Found

| Resource | Status observed | Security notes |
|---|---|---|
| Resource group `nurse-triage-rg` | Exists in Canada Central | No tags observed on resource group. |
| Container App `nurse-triage-api` | Public HTTPS ingress, insecure HTTP disabled, 1-3 replicas | Identity type is `None`; ACR registry username/password is used; many secrets are plain env values; dashboard shell is public. |
| Container Apps environment `nurse-triage-aca-env` | App logs destination is Log Analytics | mTLS disabled; public network access enabled; no VNet configuration; zone redundancy disabled. |
| ACR `nursetriageacr7351` | Basic SKU, public network access enabled | Admin user enabled; export policy enabled; quarantine, retention, soft delete, and trust policy disabled. |
| PostgreSQL Flexible Server `nurse-triage-pg-7351` | Ready, public network access enabled, version 14 | Password auth enabled; Entra auth disabled; private endpoints absent; geo-redundant backup disabled; HA disabled; backup retention 7 days; storage autogrow disabled. |
| Storage account `nursetriage840442` | StorageV2, Standard_LRS, TLS 1.2, public blob access disabled | Network default action Allow; Shared Key access property is unset, which means Shared Key remains allowed; no identity; no delete retention policy observed. |
| Log Analytics workspace `workspacenursetriagergae96` | Exists | Container Apps environment points to Log Analytics; retention and diagnostic coverage could not be fully confirmed due intermittent Azure API resets. |

### Live Endpoint Checks

`scripts/azure-verify.ps1` passed 10/10 checks against:

`https://nurse-triage-api.livelymushroom-186460d5.canadacentral.azurecontainerapps.io`

Observed:

- `/`, `/health`, `/ready`, `/metrics` returned HTTP 200.
- `/ready` confirmed database connectivity.
- TLS certificate validation passed.
- `X-Request-ID` header is present.
- Twilio route exists and returns HTTP 403 without a valid Twilio signature, which is expected.

Observed security headers on `/health`:

- Present: `X-Request-ID`
- Missing or not observed: `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options` or CSP frame policy, `Referrer-Policy`, `Content-Security-Policy`, `Permissions-Policy`

### Repository Controls Already In Place

| Control area | In place | Evidence |
|---|---|---|
| Deployment docs and scripts | Yes | `docs/AZURE_DEPLOYMENT.md`, `DEPLOYMENT.md`, `scripts/azure-deploy.ps1`, `scripts/azure-update-secrets.ps1`, `scripts/azure-verify.ps1` |
| Production config validation | Yes | `src/config.py` requires Postgres, API key, DB URL, and Twilio token when applicable. |
| Twilio request authentication | Yes | `src/security/twilio_signature.py`; live unauthenticated webhook returned HTTP 403. |
| Rate limiting | Yes | `src/security/middleware.py` with `RATE_LIMIT=60/minute`. |
| Safe error handling | Yes | `src/security/middleware.py` hides stack traces in production. |
| Structured logs and correlation IDs | Yes | `src/observability/logging.py`, `src/main.py`; live `X-Request-ID` observed. |
| PHI masking | Yes | `src/safety/phi_masking.py`, `src/storage/postgres.py`, `src/api/dashboard_privacy.py`. |
| Sentry PHI scrubbing | Yes, if configured | `src/observability/sentry_integration.py`, `MONITORING.md`; DSN not observed in live Container App env list. |
| CI security scanning | Partial | `.github/workflows/ci.yml` runs Gitleaks, Bandit, Safety; Bandit/Safety currently use `|| true`, so findings are reported but do not fail the build. |
| Local secret scanning | Partial | `.gitleaks.toml`, `.pre-commit-config.yaml`; pre-commit includes gitleaks, but the local `no-inline-secrets` hook appears to fail every matching text file rather than scanning a pattern. |
| Non-root container | Yes | `Dockerfile` creates and runs as `appuser`. |
| App health verification | Yes | `scripts/azure-verify.ps1` and live check passed. |

## Requirements Matrix

| Requirement | Current status | Gap | Remediation |
|---|---|---|---|
| Use managed identity for Azure resource access | Not met | Container App identity is `None`; ACR pull uses registry username/password. | Enable system-assigned or user-assigned managed identity. Assign least-privilege roles: `AcrPull`, `Key Vault Secrets User`, `Storage Blob Data Contributor` or narrower. |
| Store production secrets in Key Vault | Not met | Several production secrets are plain env values; only `AZURE_STORAGE_CONNECTION_STRING` is a Container App secret reference. | Create production Key Vault with RBAC, soft delete, purge protection, private endpoint where possible. Move DB, DeepSeek, Twilio, Sentry, dashboard token, and storage secrets to Key Vault references. |
| Rotate exposed or control-plane-visible credentials | Completed by operator | Current secrets were visible through read-only control-plane output before containment. | No further rotation is part of this remediation unless a future compromise is discovered. Verify current runtime values are stored as Container App secret refs or Key Vault references. |
| Disable ACR admin account | Not met | ACR admin user is enabled. | Switch Container App image pull to managed identity with `AcrPull`, then disable ACR admin credentials. No additional password rotation is part of this remediation unless future compromise is discovered. |
| Limit ACR public exposure | Not met | ACR public network access enabled; Basic SKU does not support Private Link. | Upgrade to Premium, configure private endpoint if feasible, disable public network access, disable export policy when public access is disabled. If ACA public pull requires exception, document and compensate with admin disabled and Defender scanning. |
| Scan container images for vulnerabilities | Partial | CI has Safety/Bandit, but Defender for Cloud is not registered and image scanning is not confirmed. | Register `Microsoft.Security`; enable Defender CSPM/Containers or registry image vulnerability assessment; fail CI on critical/high image dependency issues after triage period. |
| Use private database access | Not met | PostgreSQL public network access is enabled; no private endpoint; password auth enabled; Entra auth disabled. | Add VNet integration/private endpoint or private access for ACA to PostgreSQL; disable public network access after validation; enable Entra authentication and migrate app to managed identity/token auth where feasible. |
| Harden database durability | Partial | Backup retention 7 days; geo-redundant backup disabled; HA disabled; storage autogrow disabled. | Set backup retention per compliance need, likely 14-35 days; enable geo-redundant backup for production if regional DR is required; enable HA and storage autogrow if uptime requirements justify cost. |
| Enforce TLS for database | Mostly met | App DB URL uses `sslmode=require`; Azure PostgreSQL requires TLS. | Move connection string to Key Vault and consider client cert/root validation mode where supported by driver and platform. |
| Restrict storage account access | Not met | Storage network default action is Allow; Shared Key is unset/allowed; no private endpoint; no delete retention observed. | Migrate app to managed identity + Azure RBAC for Blob access, then disable Shared Key, set network default Deny/private endpoint, enable blob soft delete/versioning/immutability as retention policy requires. |
| Centralize resource logs | Partial | ACA environment sends logs to Log Analytics; resource diagnostics for ACR/PostgreSQL/Storage/Key Vault need verification. | Create diagnostic settings for every resource to Log Analytics: Container App, ACA environment, ACR, PostgreSQL, Storage blob service, Key Vault, Azure Activity Log. Set retention and alert rules. |
| Enable Defender and compliance posture | Not met | `az security pricing list` returned Microsoft.Security not registered; no policy assignments at subscription scope. | Register Microsoft.Security, enable Defender for Cloud plans, turn on MCSB/regulatory standards, review Secure Score weekly. |
| Enforce governance with Azure Policy | Not met | No subscription policy assignments returned. | Assign built-in policies for Key Vault purge protection, Storage shared key disabled, Storage public network disabled, ACR admin disabled, diagnostics required, private endpoints required, allowed locations/SKUs/tags. |
| Protect admin/dashboard surface | Partial | Dashboard API requires token in production but shell route is public; `DASHBOARD_ADMIN_TOKEN` not observed. | Set `DASHBOARD_ADMIN_TOKEN` in Key Vault or disable `DASHBOARD_ENABLED=false`. Add Entra auth via Azure Front Door/App Gateway/Easy Auth equivalent or keep dashboard behind VPN/private ingress. |
| Add HTTP security headers | Not met | Live response lacks standard browser security headers. | Add FastAPI middleware for HSTS, nosniff, CSP/frame restrictions, referrer policy, permissions policy, and cache control for sensitive endpoints. |
| Secret scanning and repo history hygiene | Partial | Gitleaks configured; GitHub secret scanning/manual settings documented; historical secret issue documented as blocking. | Confirm rotation evidence is retained, complete git history cleanup or document risk acceptance, enable GitHub secret scanning/push protection/Dependabot. Fix pre-commit hook. |
| Incident response readiness | Partial | `SECURITY.md`, `SECURITY_CLEANUP.md`, monitoring docs exist. | Add Azure-specific incident runbooks: key compromise, PHI exposure, suspicious ACR pull, database access anomaly, Twilio webhook abuse, storage exfiltration. |
| Compliance/privacy alignment | Partial | PHI masking exists; STORE_PHI defaults false; dashboard masks payloads. | Define retention schedule, legal basis/BAA posture for Azure/Twilio/Sentry/DeepSeek, data residency requirements, audit evidence collection, access reviews, and breach notification workflow. |

## Remediation Plan

### Phase 0: Immediate Containment, Same Day

Owner: platform/security operator

Goal: remove the most urgent credential and exposure risk without changing application behavior.

1. Confirm rotation completion evidence is stored in the restricted evidence pack.
2. Replace plain Container App env values with Container App secret references as a stopgap.
3. Remove all plaintext values from `scripts/fix-env-vars.ps1` output behavior or retire the script because it prints current env values and targets an older East US resource group.
4. Confirm `DASHBOARD_ADMIN_TOKEN` is set as a secret or set `DASHBOARD_ENABLED=false` in production until dashboard auth is finalized.
5. Re-run `scripts/azure-verify.ps1`, `scripts/azure-security-verify.ps1`, and a Twilio signed-call smoke test.

Acceptance criteria:

- `az containerapp show` reports secret refs for every sensitive env var, not plain values.
- Rotation completion is recorded by the operator.
- `/ready` is healthy after secret reference migration.
- Dashboard API is inaccessible without auth and either shell is protected or disabled.

### Phase 1: Identity And Secret Modernization, Days 1-3

Owner: platform engineer

Goal: remove long-lived Azure credentials from the app runtime.

1. Create a production Key Vault:
   - RBAC permission model
   - Soft delete enabled
   - Purge protection enabled
   - Diagnostic logs to Log Analytics
   - Private endpoint or network restrictions where feasible
2. Enable managed identity on `nurse-triage-api`.
3. Grant least-privilege roles:
   - Key Vault: `Key Vault Secrets User`
   - ACR: `AcrPull`
   - Storage: `Storage Blob Data Contributor` or narrower custom role for report container
4. Convert Container App secrets to Key Vault references:
   - `DATABASE_URL`
   - `DEEPSEEK_API_KEY`
   - `TWILIO_AUTH_TOKEN`
   - `SENTRY_DSN`
   - `DASHBOARD_ADMIN_TOKEN`
   - temporary storage connection string if still needed
5. Update deployment scripts to create identity, Key Vault, role assignments, and Key Vault refs. Do not pass secrets as plain command arguments where avoidable.
6. Move ACR image pull to managed identity and disable ACR admin user.

Acceptance criteria:

- Container App identity is not `None`.
- Container App registry configuration uses identity, not username/password.
- ACR `adminUserEnabled=false`.
- No sensitive env var has a plain `value` in Container App config.

### Phase 2: Network Isolation, Days 3-7

Owner: cloud/network engineer

Goal: limit data-plane access to trusted Azure paths.

1. Design ACA VNet integration for the Container Apps environment.
2. Add private access for PostgreSQL:
   - Preferred: private endpoint or private access with private DNS.
   - Validate app connectivity from ACA.
   - Disable public network access after validation.
3. Harden storage:
   - Move app access to managed identity and RBAC.
   - Disable Shared Key authorization after confirming no clients need account keys.
   - Set network default action to Deny.
   - Add private endpoint/private DNS for Blob.
   - Enable blob soft delete, container soft delete, and versioning if retention requirements allow.
4. Evaluate ACR Private Link:
   - Upgrade ACR to Premium if private endpoint is required.
   - Validate ACA image pulls.
   - Disable public network access if architecture supports it.
5. Document any public-network exceptions with compensating controls and expiration dates.

Acceptance criteria:

- PostgreSQL public network access disabled or a documented temporary exception exists.
- Storage account network default is Deny or a documented temporary exception exists.
- Shared Key disabled after managed identity Blob access is live.
- Private DNS resolution is documented and tested.

### Phase 3: Monitoring, Defender, And Governance, Week 2

Owner: security/cloud admin

Goal: make posture measurable and continuously enforced.

1. Register `Microsoft.Security`.
2. Enable Defender for Cloud:
   - Microsoft Cloud Security Benchmark
   - Defender CSPM or Defender for Containers/registry scanning as budget permits
   - Defender plan for open-source relational databases if available in tenant/subscription
3. Configure diagnostic settings to Log Analytics for:
   - Azure Activity Log
   - Container App and Container Apps environment
   - ACR
   - PostgreSQL Flexible Server
   - Storage account and Blob service
   - Key Vault
4. Create alerts:
   - Container app 5xx spike
   - Readiness failures
   - Rate-limit spikes
   - Invalid Twilio signature spikes
   - ACR admin enabled or suspicious pulls
   - PostgreSQL connection failures or auth failures
   - Storage access from unexpected locations
   - Key Vault secret read anomalies
5. Assign Azure Policy at subscription/resource-group scope:
   - ACR admin account disabled
   - Storage accounts prevent Shared Key access
   - Storage public network access restricted
   - Key Vault purge protection enabled
   - Diagnostic settings required
   - Allowed locations Canada Central/approved regions
   - Required tags: `owner`, `environment`, `data-classification`, `cost-center`
   - Public access disabled for databases
6. Export Defender alerts to Log Analytics/Sentinel if Sentinel is used.

Acceptance criteria:

- Defender recommendations populate for resources.
- Policy compliance dashboard shows assigned controls.
- Log Analytics contains logs from every production resource.
- Alerts are tested with at least one synthetic event.

### Phase 4: Application Security Hardening, Week 2

Owner: application engineer

Goal: close browser/API hardening gaps and clean up security tooling.

1. Add security header middleware:
   - `Strict-Transport-Security: max-age=31536000; includeSubDomains`
   - `X-Content-Type-Options: nosniff`
   - `Referrer-Policy: no-referrer`
   - `Content-Security-Policy` with `frame-ancestors 'none'` or trusted dashboard host
   - `Permissions-Policy` with unnecessary browser features disabled
   - Cache-control headers for sensitive API responses
2. Decide production dashboard posture:
   - Preferred: Entra-authenticated front door or private-only admin surface.
   - Interim: keep API token auth, set `DASHBOARD_ADMIN_TOKEN`, and restrict shell route.
3. Fix `.pre-commit-config.yaml`:
   - Keep Gitleaks.
   - Replace the always-failing `no-inline-secrets` local hook with a real scanner or remove it.
4. Make CI security jobs actionable:
   - Keep artifact upload.
   - Fail on high-confidence Bandit findings after baseline triage.
   - Replace deprecated `safety check` if needed with the current Safety CLI behavior.
5. Add tests for:
   - Security headers.
   - Dashboard shell/API auth in production.
   - Secret ref validation helper if deployment scripts are tested.

Acceptance criteria:

- Header check shows expected headers.
- Production dashboard route cannot expose data or shell without approved access path.
- Pre-commit can run successfully on a clean commit.
- CI blocks new critical/high security issues once baseline is approved.

### Phase 5: Resilience, Compliance, And Evidence, Weeks 3-4

Owner: product owner, compliance lead, platform engineer

Goal: make the system pilot/compliance ready with durable evidence.

1. Define production data retention:
   - Triage sessions
   - Masked transcripts
   - SBAR/report blobs
   - Logs and Sentry events
   - Backups
2. Set database backup and HA posture:
   - Retention target, likely 14-35 days depending on pilot and compliance needs.
   - Geo-redundant backup decision.
   - HA decision and RTO/RPO targets.
3. Set storage lifecycle policies:
   - Retention, legal hold/immutability if required.
   - Soft delete/versioning.
   - Lifecycle archive/delete rules.
4. Confirm third-party privacy posture:
   - Azure region and tenant
   - Twilio
   - DeepSeek or chosen LLM provider
   - Sentry
   - Any BAA/DPA requirements
5. Create evidence pack:
   - Architecture diagram
   - Data flow and trust boundaries
   - Asset inventory
   - Risk register
   - Access review evidence
   - Security test results
   - Incident runbooks
   - Backup/restore test
   - Azure Policy and Defender export

Acceptance criteria:

- RTO/RPO documented and tested.
- Retention schedule approved.
- Restore test completed.
- Evidence pack ready for stakeholder or external review.

## Recommended Deployment Script Changes

Update or replace `scripts/azure-deploy.ps1` and `scripts/azure-deploy.sh` so new environments are secure by default:

1. Create Key Vault and managed identity.
2. Store secrets in Key Vault, not direct Container App secret values.
3. Configure Container App with Key Vault references.
4. Assign identity to ACR pull instead of using ACR admin credentials.
5. Create Log Analytics workspace explicitly with desired retention.
6. Create diagnostic settings for all resources.
7. Use PostgreSQL version 16 or explicitly document version 14 if retained.
8. Prefer private networking by default, with a documented public-dev mode.
9. Remove or gate any script path that prints current environment variable values.
10. Output only resource names, secret names, and health status. Never output secret values.

## Validation Commands

Run after remediation. Do not paste command output into tickets if it contains secret values.

```powershell
# App health
.\scripts\azure-verify.ps1 -BaseUrl "https://nurse-triage-api.livelymushroom-186460d5.canadacentral.azurecontainerapps.io"

# Container App should use identity and secret refs only
az containerapp show -g nurse-triage-rg -n nurse-triage-api `
  --query "{identity:identity, registries:properties.configuration.registries, env:properties.template.containers[0].env[].{name:name,hasValue:!!value,secretRef:secretRef}}"

# ACR admin should be disabled
az acr show -g nurse-triage-rg -n nursetriageacr7351 `
  --query "{adminUserEnabled:adminUserEnabled,publicNetworkAccess:publicNetworkAccess,policies:policies}"

# PostgreSQL network/auth posture
az resource show --ids "/subscriptions/ec9a62a8-7a25-4f57-b9a5-7e6249236eaf/resourceGroups/nurse-triage-rg/providers/Microsoft.DBforPostgreSQL/flexibleServers/nurse-triage-pg-7351" `
  --api-version 2023-06-01-preview `
  --query "{network:properties.network,authConfig:properties.authConfig,backup:properties.backup,highAvailability:properties.highAvailability}"

# Storage shared key/network posture
az storage account show -g nurse-triage-rg -n nursetriage840442 `
  --query "{allowSharedKeyAccess:allowSharedKeyAccess,publicNetworkAccess:publicNetworkAccess,networkRuleSet:networkRuleSet,minimumTlsVersion:minimumTlsVersion,allowBlobPublicAccess:allowBlobPublicAccess}"

# Defender for Cloud registration
az provider show -n Microsoft.Security --query registrationState -o tsv
az security pricing list --query "value[].{name:name,pricingTier:pricingTier,subPlan:subPlan}"

# Policy assignments
az policy assignment list --scope "/subscriptions/ec9a62a8-7a25-4f57-b9a5-7e6249236eaf" `
  --query "[].{name:name,displayName:displayName,scope:scope,enforcementMode:enforcementMode}"
```

## Open Questions

1. What compliance target is required for pilot: HIPAA-aligned internal controls only, formal HIPAA program, PIPEDA/PHIPA, SOC 2, or another framework?
2. What is the required RTO/RPO for nurse triage operations?
3. Should the dashboard be public behind strong auth, private-only, or disabled in production?
4. Is the LLM provider approved for handling any data that may contain PHI, even after masking?
5. What is the approved data retention period for transcripts, SBAR reports, logs, and backups?
6. Are Canada-only data residency controls required for every dependency?

## Microsoft References Used

- Azure Container Apps security overview: https://learn.microsoft.com/en-us/azure/container-apps/security
- Manage secrets in Azure Container Apps: https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets
- Azure Container Registry authentication: https://learn.microsoft.com/en-us/azure/container-registry/container-registry-authentication
- Managed identity authentication for ACR: https://learn.microsoft.com/en-us/azure/container-registry/container-registry-authentication-managed-identity
- ACR data loss prevention and export policy: https://learn.microsoft.com/en-us/azure/container-registry/data-loss-prevention
- Secure Azure Database for PostgreSQL Flexible Server: https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/security-overview
- PostgreSQL TLS: https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/security-tls
- PostgreSQL Microsoft Entra authentication: https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/security-entra-concepts
- PostgreSQL backup and restore: https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-backup-restore
- Prevent Shared Key authorization for Azure Storage: https://learn.microsoft.com/en-us/azure/storage/common/shared-key-authorization-prevent
- Azure Monitor diagnostic settings: https://learn.microsoft.com/en-us/azure/azure-monitor/essentials/diagnostic-settings
- Microsoft Cloud Security Benchmark in Defender for Cloud: https://learn.microsoft.com/en-us/azure/defender-for-cloud/concept-regulatory-compliance
- Defender for Containers overview: https://learn.microsoft.com/en-us/azure/defender-for-cloud/defender-for-containers-introduction
- Secure Azure Key Vault: https://learn.microsoft.com/en-us/azure/key-vault/general/secure-key-vault
