# PR 5 Final Validation Results — <YYYY-MM-DD>

Operator: <name> | Build: <git SHA> | Pre-flight re-run: PASS/FAIL
Sentry alerts verified firing: LLM timeout [ ] JSON failure [ ] post-check [ ] DB error [ ]

## Summary

| Block | Calls | Pass | Fail |
|---|---|---|---|
| A Birchwood (15) | | | |
| B Insurance (5) | | | |
| C Healthcare regression (5) | | | |
| D Adversarial (5) | | | |
| E Silence/unclear (5) | | | |

## Per-call results

| ID | CallSid | PASS/FAIL | Expected | Observed | Dashboard checks (record/status/audit) |
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
| A11 | | | | | |
| A12 | | | | | |
| A13 | | | | | |
| A14 | | | | | |
| A15 | | | | | |
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

## Cost measurement

Avg call duration (s): ____  |  Avg TTS chars: ____
Twilio voice $/min: ____  |  Azure TTS $/1M chars: ____  |  Infra $/day: ____
`python -m scripts.pilot_metrics --costs` output attached: [ ]

## Defects

| # | Severity | Call ID | Expected | Observed | Status |
|---|---|---|---|---|---|
| 1 | | | | | open |

## Sign-off (pilot gate)

- [ ] Zero critical defects open
- [ ] Healthcare regression identical to baseline
- [ ] All Sentry alerts verified firing
- [ ] Cost assumptions entered in docs/pilot/BIRCHWOOD_PRICING_ASSUMPTIONS.md
