# Birchwood Escalation Workflow

How flagged situations move from the call to a human, tied to the
dashboard's record statuses.

## Injury branch (highest priority)

On the call (automatic, deterministic, cannot be disabled):
1. Caller mentions an injury (or answers yes to "was anyone hurt?") →
   ORCA immediately advises seeking medical attention / 9-1-1, exactly once.
2. The record is flagged `injuries_reported`, forced to human review, and
   lands on the dashboard **pinned at the top, status `escalated`**.

In the shop (human, same business day — sooner if the call is recent):
3. The dashboard watcher calls the customer back FIRST to confirm everyone
   is okay — before any repair conversation. ORCA gives no medical advice
   beyond the 9-1-1 line, and neither should the shop.
4. Set status `contacted` with a note ("confirmed customer is okay" /
   "customer getting checked out"). Proceed to scheduling only when
   appropriate; status `scheduled` / `completed` as usual.
5. If the callback raises any concern (injury sounds serious, customer
   unreachable), escalate to the shop manager; note it on the record.

## Transfer requests (non-injury)

When a caller says "transfer" or presses 0, what happens depends on the
`BIRCHWOOD_TRANSFER_NUMBER` config (env-only — the dial target is never
taken from the call payload):

- **Configured:** the intake record is persisted first, then ORCA dials
  the Birchwood transfer line live (`<Dial>`). If the dial is busy,
  unanswered, or fails, the caller hears an honest callback close ("I
  wasn't able to connect you just now — I'll have one of our advisors
  call you right back") — the persisted record backs that promise. The
  record's `birchwood_transfer` metadata carries `attempted` and the
  final `dial_status`.
- **Unconfigured (default):** no dial is attempted. The caller hears the
  callback close and the record lands in the dashboard callback queue.

The watcher checks `dial_status`: `completed` means the hand-off
happened live; anything else means the callback promise is theirs to
keep — call the customer back and complete the record.

## System-failure callbacks

When ORCA hits an internal failure mid-call it apologizes, promises a
callback, and the record is flagged `incomplete` + `system_error`
(fail-closed contract from PR 1). These appear as escalated/incomplete
records: **the promise is kept by the watcher** — call the customer back,
collect what's missing, set statuses as usual. The operator investigates
the underlying failure in parallel.

## Human review flow

`human_review_required` (injury, readback correction, uncertain details):
the record is not actionable until a human has read the transcript against
the structured record. The watcher reviews, fixes their understanding (the
record itself is immutable; corrections live in the callback conversation
and the status notes), then proceeds. Every step is captured by the status
audit trail — that's the pilot's accountability record.
