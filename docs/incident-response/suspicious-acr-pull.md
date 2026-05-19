# Suspicious ACR Pull Runbook

## Symptoms And Signals

- ACR pull from unexpected identity, IP, region, or time window.
- ACR admin account usage after it should be disabled.
- Defender for Cloud container registry alert.
- Unexpected Container App image or revision change.

## Immediate Containment

- Preserve ACR diagnostic logs and Azure Activity Log.
- Confirm whether ACR admin user is enabled.
- Disable ACR admin credentials if managed identity pull is validated.
- Restrict or remove suspicious role assignments.

## Evidence To Preserve

- ACR repository, tag, digest, pull timestamp, and requester identity.
- Activity Log for ACR credential, role, and repository changes.
- Container App revision and image digest.
- CI/CD logs that built or deployed the image.

## Rotation Or Revocation

Rotation is only for a future incident. Current remediation rotation has already
been completed.

- Rotate ACR admin passwords only if admin credentials were enabled and may have
  been used.
- Revoke suspicious service principals, role assignments, or tokens.
- Rebuild and redeploy trusted images if image integrity is uncertain.

## Communication And Escalation

- Notify security, platform owner, and release owner.
- Escalate if a production image may have been modified or pulled externally.
- Contact Azure support if ACR logs indicate unexplained platform behavior.

## Recovery Validation

- Container App pulls through managed identity.
- ACR admin user is disabled.
- Running image digest matches trusted build output.
- Defender and diagnostic logs are present.

## Post-Incident Prevention

- Enforce ACR admin disabled with Azure Policy.
- Enable Defender for Containers/registry assessment.
- Require signed or provenance-verified images if supported by the release path.
- Review least-privilege ACR roles.
