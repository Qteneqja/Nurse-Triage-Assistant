# PHI Exposure Runbook

## Symptoms And Signals

- PHI appears in logs, traces, Sentry, dashboards, tickets, or exports.
- User reports receiving another caller's information.
- Storage, database, or report access from unexpected identity or location.
- Failed masking test or dashboard privacy regression.

## Immediate Containment

- Stop sharing the affected artifact immediately.
- Restrict access to the affected log, blob, ticket, or dashboard record.
- Preserve original evidence in a restricted incident location.
- If active exposure continues, disable the affected route, export, or job.

## Evidence To Preserve

- Artifact path, record ID, session ID, and timestamps.
- Access logs for the affected artifact.
- Relevant app logs with PHI redacted in the incident ticket.
- Code version and Container App revision.

## Rotation Or Revocation

Rotation is only for future incidents where credential misuse is suspected.
Current remediation rotation has already been completed.

- Revoke affected user sessions or dashboard tokens if access control failed.
- Rotate provider keys only if exposure involved a credential.
- Preserve evidence before deleting or redacting affected artifacts.

## Communication And Escalation

- Notify security, privacy/compliance, clinical leadership, and product owner.
- Determine whether contractual, provincial, or federal notification duties apply.
- Keep incident ticket updates factual and avoid pasting PHI.

## Recovery Validation

- Confirm PHI is removed or access-restricted in logs, blobs, tickets, and tools.
- Re-run dashboard privacy tests and relevant pytest suites.
- Verify masking still applies to logs, dashboard payloads, and Sentry events.

## Post-Incident Prevention

- Add regression tests for the missed PHI pattern.
- Tighten export filters and dashboard masking.
- Review retention and deletion controls.
- Update training for evidence handling.
