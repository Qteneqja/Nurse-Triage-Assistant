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

## Urgent records (non-injury)

Transfers (`TRANSFER_COLLISION_CENTER` — undrivable or caller pressed 0)
arrive `escalated`. The live transfer already happened or was attempted;
the watcher confirms the hand-off happened and completes the record.

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
