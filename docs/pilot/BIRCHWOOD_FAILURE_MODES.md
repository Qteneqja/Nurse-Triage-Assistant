# Failure-Mode Response Plan — Birchwood Pilot

For each outage: what the caller experiences, what Birchwood is told, and
how the operator recovers. The system's standing contract (PR 1): any
mid-call failure ends with an apology + a callback promise + a flagged
incomplete record — never a crash, never silent loss.

## 1. Application failure (bad deploy, crash)

- **Caller hears:** mid-call — "I'm so sorry — we've hit a technical
  problem... someone from the team will call you back shortly." New calls
  during full downtime hit Twilio's error tone (see §2 mitigation).
- **Birchwood is told:** "We had a brief technical issue between TIME and
  TIME; N calls were affected and each is flagged on your dashboard for a
  callback. No information was lost."
- **Recovery:** /health check → restart revision or roll back
  ([../ROLLBACK_PROCEDURE.md](../ROLLBACK_PROCEDURE.md)) → work the
  flagged records (escalation workflow §system-failure callbacks).

## 2. Twilio outage (or webhook unreachable)

- **Caller hears:** ring-no-answer or carrier error; nothing we control.
- **Mitigation (set up before pilot):** a Twilio **fallback URL** on the
  Birchwood number pointing at a static TwiML Bin: "Thanks for calling
  Birchwood Collision. We're having a brief technical issue — please call
  back shortly, or leave your name and number with the main line."
- **Birchwood is told:** "Calls to the intake number may not connect; the
  carrier (Twilio) is having an incident (status.twilio.com). Your main
  line is unaffected."
- **Recovery:** none on our side; verify webhooks resume, then check for a
  gap in records and tell Birchwood the affected window.

## 3. Database outage (Postgres)

- **Caller hears:** the fail-closed apology + callback promise (storage
  errors mid-call are caught); brand-new calls may fail to start (Twilio
  fallback URL covers them).
- **What's preserved:** everything persisted up to the failure; the
  in-flight turn may be lost — the flagged record marks it.
- **Birchwood is told:** same script as §1.
- **Recovery:** Azure Database for PostgreSQL status; restart/failover;
  `/ready` flips back to 200; reconcile flagged records.

## 4. LLM provider outage (DeepSeek)

- **Birchwood impact: NONE.** The collision flow is fully deterministic —
  no LLM in the live path. No caller-facing change, no script needed.
- (Healthcare dynamic intake would fail closed to nurse escalation — its
  own runbook covers that; healthcare is not in this pilot.)

## 5. Azure Speech (TTS) degradation

- **Caller hears:** ORCA's voice falls back to the telephony voice
  (Polly) automatically; the conversation continues. No action needed
  beyond noting it in the pilot log.

## Communication rules

- Birchwood gets ONE named contact and plain-language updates: what
  happened, the affected window, what we're doing, when fixed. No internal
  jargon, no blame.
- Every incident goes in the pilot log the same day.
