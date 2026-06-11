# PR 1 Staging Validation Results — <YYYY-MM-DD>

Operator: <name> · Staging build: <git SHA / image tag> · Twilio number(s): <redacted>
Signature validation pre-flight: PASS / FAIL (forged webhook returned: <code>)

## Summary

| Block | Calls | Pass | Fail | Notes |
|---|---|---|---|---|
| A — Birchwood (10) | | | | |
| B — Long-story (5) | | | | |
| C — Silence/unclear (5) | | | | |
| D — Adversarial (5) | | | | |
| E — Healthcare regression (5) | | | | |

## Per-call results

<!-- One row per call. Copy CallSid from Twilio console; never paste caller PII. -->

| ID | CallSid | Result (PASS/FAIL) | Expected | Observed | Record checks (flags / narrative / finalization_reason) |
|---|---|---|---|---|---|
| A1 | | | | | |
| A2 | | | | | |
| A3 | | | | | |
| A4 | | | | | |
| A5 | | | | | |
| A6 | | | | | |
| A7 | | | | | |
| A8 | | | | | |
| A9 | | | | | |
| A10 | | | | | |
| B1 | | | | | |
| B2 | | | | | |
| B3 | | | | | |
| B4 | | | | | |
| B5 | | | | | |
| C1 | | | | | |
| C2 | | | | | |
| C3 | | | | | |
| C4 | | | | | |
| C5 | | | | | |
| D1 | | | | | |
| D2 | | | | | |
| D3 | | | | | |
| D4 | | | | | |
| D5 | | | | | |
| E1 | | | | | |
| E2 | | | | | |
| E3 | | | | | |
| E4 | | | | | |
| E5 | | | | | |

## Defects

| # | Severity (critical/high/medium/low) | Call ID | Expected | Observed | Log excerpt (scrubbed) | Status |
|---|---|---|---|---|---|---|
| 1 | | | | | | open |

## Sign-off

- [ ] Zero critical defects open
- [ ] Healthcare regression block identical to baseline
- [ ] Results reviewed and defects filed back to the PR 1 branch
