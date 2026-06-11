# Birchwood Pilot Runbook (2–4 weeks)

Who does what, daily, while ORCA answers Birchwood collision-intake calls.

## Roles

- **Operator (you):** owns the system — deploys, monitoring, defects.
- **Birchwood dashboard watcher:** shop staff member who works the records
  queue (their normal callback workflow, now fed by ORCA).
- **On-call:** the operator, reachable during shop hours for the pilot.

## Daily checks (operator, ~5 minutes, each morning)

1. `curl $BASE/health` and `curl $BASE/ready` → 200/200, postgres connected.
2. Sentry: zero new errors overnight (or triage them).
3. `python -m scripts.pilot_metrics` (via `az containerapp exec`) — eyeball
   drop-off % and injury count vs yesterday; investigate spikes.
4. Dashboard records list loads; spot-check yesterday's last record.
5. Twilio console: no webhook error spikes on the Birchwood number.

## Working the dashboard (Birchwood staff)

- Sign in at `/dashboard/login` with the token you were given.
- Work `/dashboard/records` top-down: **red rows first** (injury/urgent —
  they arrive already `escalated`), then `new`.
- For each record: read the shop summary → call the customer back at the
  listed number → set the status (`contacted`, then `scheduled` /
  `completed`). Enter your name with each change — it's the audit trail.
- **Injury-flagged record:** see
  [BIRCHWOOD_ESCALATION_WORKFLOW.md](BIRCHWOOD_ESCALATION_WORKFLOW.md) —
  call the customer FIRST, confirm everyone is okay, before any repair talk.
- A record marked `INCOMPLETE_CALLBACK_NEEDED` means ORCA couldn't capture
  everything (silence, missing claim number) — the missing items are listed
  on the record.

## When something looks wrong

- One bad call → open the record, check transcript + flags; file it with
  the operator (session id, what's wrong).
- Calls not appearing at all → operator: Twilio webhook config, then
  [BIRCHWOOD_FAILURE_MODES.md](BIRCHWOOD_FAILURE_MODES.md).
- Bad deploy → [../ROLLBACK_PROCEDURE.md](../ROLLBACK_PROCEDURE.md).

## On-call basics (operator)

- Sentry alert → check /health + /ready → if down, ACA revision restart;
  if a deploy caused it, roll back; if Twilio/LLM/DB outage, follow the
  failure-mode plan and notify Birchwood's contact with the script there.
- Keep a pilot log (date, incident, calls affected, action) — it feeds the
  end-of-pilot review.

## Weekly (operator)

- Run `python -m scripts.pilot_metrics --costs --json > eval_reports/pilot_week_N.json`.
- Sample 5 records vs their transcripts for extraction accuracy + injury
  correctness (metrics doc, manual-review procedure).
- Share the week's numbers with Birchwood against the success targets.
