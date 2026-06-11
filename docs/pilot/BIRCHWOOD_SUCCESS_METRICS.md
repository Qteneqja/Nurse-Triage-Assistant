# Birchwood Pilot Success Metrics (2–4 weeks)

Every metric is computable from stored data:
`python -m scripts.pilot_metrics` (run weekly; `--json` for the archive).
Targets are starting points — agree on final numbers with Birchwood at
pilot kickoff.

| # | Metric | How it's computed | Target |
|---|---|---|---|
| 1 | % calls completing intake without human rescue | `completed_without_rescue_pct`: finalized + COMPLETED_INTAKE + no human-review flag + no readback correction | ≥ 60% |
| 2 | Extraction accuracy on required fields | Manual review (below) of a weekly 5-record sample vs transcript | ≥ 90% fields correct |
| 3 | Average call duration | `avg_call_duration_seconds` (session start → last turn) | ≤ 5 min |
| 4 | Caller drop-off rate | `drop_off_pct`: sessions never finalized / total | ≤ 15% |
| 5 | % records requiring follow-up callback | `callback_needed_pct` | ≤ 30% |
| 6 | Injury-branch correctness | Manual review (below) of every `injuries_reported` record + spot-check of denials | 100% of injury mentions flagged; 0 missed |
| 7 | Dashboard adoption | `record_statuses`: % records moved out of new/derived within 1 business day | ≥ 90% |

## Manual review procedures

**Extraction accuracy (#2):** weekly, take the script's 5 most recent
COMPLETED records; for each, compare every required field (vehicle, when,
where, drivability, damage, injuries state, insurance path, name, phone)
against the transcript. Score = correct fields / total required fields.

**Injury correctness (#6):** the script lists every `injuries_reported`
session id. For each: read the transcript — was there a genuine injury
mention (true positive)? Also re-read 5 random `injuries_denied` records
for missed mentions (false negatives). A single missed injury mention is a
critical defect: file it immediately, do not wait for the weekly review.

## Reporting

Weekly: `python -m scripts.pilot_metrics --costs --json > eval_reports/pilot_week_N.json`,
plus the two manual-review scores, shared with Birchwood against the
targets. End of pilot: the four weekly snapshots are the evaluation.
