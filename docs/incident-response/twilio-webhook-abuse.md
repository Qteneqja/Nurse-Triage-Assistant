# Twilio Webhook Abuse Runbook

## Symptoms And Signals

- Spike in invalid Twilio signature failures.
- Unexpected call volume, repeated `CallSid` patterns, or rate-limit events.
- Twilio console alerts, billing anomalies, or webhook retries.
- Unusual source IPs before Twilio signature rejection.

## Immediate Containment

- Confirm `TWILIO_VALIDATE_SIGNATURE=true` in production.
- Check rate-limit telemetry and Container App logs.
- Temporarily tighten ingress controls or rate limits if abuse affects service.
- If a phone number is targeted, review Twilio number configuration.

## Evidence To Preserve

- Twilio request IDs, call SIDs, timestamps, and webhook URLs.
- App logs with request IDs and signature validation result.
- Azure ingress metrics and rate-limit events.
- Twilio console event and billing evidence.

## Rotation Or Revocation

Rotation is only for a future incident. Current remediation rotation has already
been completed.

- Rotate the Twilio auth token only if token compromise is suspected.
- Update Key Vault or the approved secret store.
- Confirm Container App references the secret by `secretRef`.

## Communication And Escalation

- Notify platform/security owner and operations contact.
- Contact Twilio support for suspected platform abuse or toll fraud.
- Notify clinical/product leadership if call handling was degraded.

## Recovery Validation

- Signed Twilio smoke test succeeds.
- Unsigned webhook request returns 403.
- Rate-limit and invalid-signature metrics return to baseline.
- `/health` and `/ready` remain healthy.

## Post-Incident Prevention

- Add alerting for invalid signature spikes.
- Review Twilio console access and webhook settings.
- Consider front-door filtering or private ingress patterns where feasible.
- Update abuse thresholds based on observed traffic.
