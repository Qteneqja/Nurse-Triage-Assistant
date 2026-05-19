# Storage Exfiltration Runbook

## Symptoms And Signals

- Large or unusual Blob read/list operations.
- Access from unexpected IP, identity, user agent, or region.
- Shared Key usage after managed identity migration.
- Missing, modified, or unexpectedly public report blobs.

## Immediate Containment

- Preserve Storage logs and Azure Activity Log.
- Disable public blob access if enabled.
- Restrict network rules if active exfiltration is suspected.
- Revoke suspicious SAS tokens or account keys if they are involved.

## Evidence To Preserve

- Storage diagnostic logs and metrics.
- Blob names, container names, timestamps, and requester identity.
- Azure Activity Log for network, key, SAS, and role changes.
- Container App revision and storage configuration.

## Rotation Or Revocation

Rotation is only for a future incident. Current remediation rotation has already
been completed.

- Revoke SAS tokens or rotate storage keys if Shared Key/SAS compromise is
  suspected.
- Update any temporary storage connection string in Key Vault.
- Prefer managed identity and Azure RBAC before disabling Shared Key.

## Communication And Escalation

- Notify security, platform owner, and privacy/compliance lead.
- Escalate to clinical/product leadership if report blobs may contain PHI.
- Coordinate legal or customer notifications if required.

## Recovery Validation

- Storage network default action and Shared Key posture are verified.
- Blob soft delete/versioning status is reviewed.
- Application report upload still works through the approved access path.
- No unexpected public containers or blobs remain.

## Post-Incident Prevention

- Complete managed identity Blob migration.
- Disable Shared Key access.
- Add private endpoint/private DNS where feasible.
- Add alerts for anomalous reads, SAS use, and network rule changes.
