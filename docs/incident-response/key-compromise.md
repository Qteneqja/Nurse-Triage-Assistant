# Key Compromise Runbook

## Symptoms And Signals

- Secret scanning alert from Gitleaks, GitHub, Azure, or a provider.
- Unexpected Key Vault secret reads.
- Unplanned Container App configuration changes.
- Provider alerts for unusual API usage, failed auth, or new locations.

## Immediate Containment

- Preserve the alert, commit SHA, Azure activity entry, and timestamps.
- Identify the affected key, scope, owner, and systems that used it.
- Disable or quarantine affected automation if active misuse is suspected.
- Restrict access to the relevant repo branch, Key Vault, or provider console
  while triage is active.

## Evidence To Preserve

- Secret scanning finding metadata.
- Git commit and PR history.
- Azure Activity Log and Key Vault audit logs.
- Provider usage logs.
- Container App revision and configuration change history.

## Rotation Or Revocation

Rotation is only for a future incident. Production rotation for the May 2026
remediation has already been completed.

- Revoke the exposed key at the provider.
- Create the replacement in the provider console or approved secret workflow.
- Store the replacement in Key Vault or the approved secret store.
- Confirm Container App env vars use `secretRef`, not plain values.
- Remove the exposed value from any ticket, log, or artifact where possible.

## Communication And Escalation

- Notify the security owner and platform owner.
- Notify clinical/product leadership if PHI systems may be affected.
- Open an incident ticket with severity, affected services, and timeline.

## Recovery Validation

- Run `scripts/azure-security-verify.ps1`.
- Confirm provider usage returns to expected baseline.
- Confirm `/health`, `/ready`, and signed Twilio smoke tests pass.
- Confirm no old credential is accepted.

## Post-Incident Prevention

- Add or tune secret scanning rules.
- Review Key Vault and repo access.
- Add Azure Policy guardrails if the exposure path was control-plane related.
- Update runbooks and evidence pack.
