# Database Access Anomaly Runbook

## Symptoms And Signals

- PostgreSQL login failures or successful logins from unexpected locations.
- `/ready` failures or unusual query latency.
- Unexpected data export volume or backup activity.
- Azure Defender, PostgreSQL, or Activity Log alerts.

## Immediate Containment

- Preserve PostgreSQL logs and Azure Activity Log.
- Identify source IP, identity, database user, and time window.
- Restrict public network access or firewall rules if exposure is active.
- Disable suspicious users or sessions if confirmed malicious.

## Evidence To Preserve

- PostgreSQL audit/auth logs.
- Azure Activity Log for server and networking changes.
- Container App revision and identity details.
- Database user list and role grants at time of detection.

## Rotation Or Revocation

Rotation is only for a future incident. Current remediation rotation has already
been completed.

- Rotate database passwords only if password compromise is suspected.
- Revoke suspicious users, grants, or firewall rules.
- Update `DATABASE_URL` in Key Vault or approved secret store if changed.

## Communication And Escalation

- Notify security, platform owner, and clinical/product leadership.
- Escalate to privacy/compliance if PHI access is possible.
- Open a high-severity incident if data access is confirmed.

## Recovery Validation

- `/ready` is healthy.
- Application can read/write expected records only.
- No sensitive env var has a plain Container App value.
- PostgreSQL public access and private endpoint posture are verified.

## Post-Incident Prevention

- Move PostgreSQL to private networking.
- Review backup retention, HA, and geo-backup decisions.
- Enable or tune database diagnostic settings and alerts.
- Review least-privilege database roles.
