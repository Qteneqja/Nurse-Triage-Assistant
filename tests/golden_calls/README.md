# Golden-Call Regression Framework
# Purpose: Documentation for the golden-call regression test suite
# Date Created: 2026-03-02
# Step: Production Hardening — Step 3F

## What This Is

Synthetic software regression tests that assert **disposition codes and safety flags** produced by the triage system. These are NOT clinical advice — they are automated checks that detect when system behavior drifts from verified expected outputs.

Each golden-call case defines:
- A synthetic caller transcript (test input)
- Expected system outputs (disposition, escalation flag, red flags, rules, confidence range)
- Severity level controlling pass/fail behavior

## Two Modes

### `deterministic_only` (Default — Used in CI)

- **Always runs in CI. Must always pass. No exceptions.**
- LLM calls are **hard-blocked** — any attempt raises `RuntimeError` immediately
- Tests only the deterministic safety gate and red-flag rule engine
- No API keys required
- Environment: `GOLDEN_CALL_MODE=deterministic_only DISABLE_EXTERNAL_CALLS=1`

### `full` (Manual Integration Testing)

- Runs the complete orchestrator pipeline including real LLM calls
- Requires `DEEPSEEK_API_KEY` (or equivalent) in environment
- Run manually: `GOLDEN_CALL_MODE=full pytest tests/golden_calls/ -v`
- **NOT run in CI**

## `DISABLE_EXTERNAL_CALLS=1`

Additional safety net set in CI. When enabled, **any** outbound HTTP call to an LLM API raises immediately. This is on top of the LLM patching in `deterministic_only` mode.

## How to Run

```bash
# CI mode (deterministic only, LLM blocked)
GOLDEN_CALL_MODE=deterministic_only DISABLE_EXTERNAL_CALLS=1 pytest tests/golden_calls/test_golden_calls.py -v

# Full integration mode (manual, needs API keys)
GOLDEN_CALL_MODE=full pytest tests/golden_calls/test_golden_calls.py -v
```

On Windows PowerShell:
```powershell
$env:GOLDEN_CALL_MODE="deterministic_only"; $env:DISABLE_EXTERNAL_CALLS="1"; pytest tests/golden_calls/test_golden_calls.py -v
```

## How to Add a New Case

1. Copy an existing case file from `cases/` as a template
2. Give it a unique `case_id` (format: `case_NNN_short_description`)
3. Fill in all required fields per `schema.json`
4. Validate: the test suite validates all cases against `schema.json` on startup
5. Run the suite to verify: `pytest tests/golden_calls/test_golden_calls.py -v`

## How to Update Expected Values

When clinical logic **intentionally** changes:

1. Update the affected case file(s) in `cases/`
2. Add a `notes` entry explaining:
   - **What** changed
   - **Why** (clinical rationale or engineering reason)
   - **Who** approved the change
3. Run both `deterministic_only` and `full` modes to verify
4. Commit with a clear message referencing the clinical change

## Severity Levels

| Severity | On Failure | On Unexpected Extra Flags/Rules |
|----------|-----------|-------------------------------|
| `critical` | **Test suite fails** (`pytest.fail`) | **Test fails** |
| `high` | **Test suite fails** (`pytest.fail`) | **Test fails** |
| `medium` | Warning only (`warnings.warn`) | Warning only |
| `low` | Warning only (`warnings.warn`) | Warning only |

## Case Files (30 Cases)

### Bucket 1 — Critical Escalation (ER_NOW)

| Case | Description | Flags Triggered | Severity |
|------|-------------|-----------------|----------|
| `case_002_chest_pain_escalation` | 60M, crushing chest pain + arm radiation | `rf_cardiac_arrest_signs` | critical |
| `case_003_breathing_difficulty` | 35F, sudden SOB, can't complete sentences | `rf_severe_breathing_failure` | critical |
| `case_006_stroke_symptoms` | 72M, facial drooping, arm weakness, slurred speech | `rf_stroke_signs` | critical |
| `case_007_anaphylaxis` | 25F, throat swelling shut after peanuts | `rf_anaphylaxis` | critical |
| `case_008_uncontrolled_bleeding` | 40M, arterial bleed won't stop | `rf_uncontrolled_bleeding` | critical |
| `case_009_loss_of_consciousness` | 65M, collapsed, unresponsive | `rf_loss_of_consciousness` | critical |
| `case_010_suicidal_ideation` | 34M, wants to end it all, has pills | `rf_suicidal_self_harm` | critical |
| `case_011_multi_critical_cardiac_breathing` | 55M, crushing chest pain AND can't breathe | `rf_cardiac_arrest_signs` + `rf_severe_breathing_failure` | critical |

### Bucket 2 — Weighted Combo (URGENT, threshold ≥ 10)

| Case | Description | Flags (weight) | Total | Severity |
|------|-------------|----------------|-------|----------|
| `case_012_high_fever_severe_pain` | 42F, 104°F + pain 9/10 | `rf_high_fever`(5) + `rf_severe_pain`(5) | 10 | critical |
| `case_014_pediatric_high_fever` | 2-month-old baby, fever, getting worse | `rf_pediatric_high_fever`(8) + `rf_worsening_symptoms`(4) | 12 | critical |
| `case_015_diabetic_emergency` | 58M, blood sugar very high, confused | `rf_diabetic_emergency`(6) + `rf_altered_mental_status`(6) | 12 | critical |
| `case_016_immunocompromised_fever` | 45F, on chemo, fever, getting worse | `rf_immunocompromised_fever`(7) + `rf_worsening_symptoms`(4) | 11 | critical |

### Bucket 3 — Sub-threshold Flags (HUMAN_REVIEW)

| Case | Description | Flags (weight) | Total | Severity |
|------|-------------|----------------|-------|----------|
| `case_005_confused_caller` | Disorganized speech, can't state name | `rf_altered_mental_status`(6) | 6 | critical |
| `case_013_pregnancy_complication` | 30F, pregnant, vaginal bleeding | `rf_pregnancy_complication`(7) | 7 | critical |
| `case_017_single_weighted_below_threshold` | 35M, severe back pain 8/10 | `rf_severe_pain`(5) | 5 | high |
| `case_029_high_fever_only` | 60M, 103°F fever, no other symptoms | `rf_high_fever`(5) | 5 | high |
| `case_030_worsening_with_fever` | 48F, fever rising to 103, getting worse | `rf_high_fever`(5) + `rf_worsening_symptoms`(4) | 9 | high |

### Bucket 4 — No Flags / Low Acuity (HUMAN_REVIEW in deterministic mode)

| Case | Description | Severity |
|------|-------------|----------|
| `case_001_insufficient_info` | 45M, cough 4 days, minimal info | critical |
| `case_004_mild_cough_low_acuity` | 28F, mild cough 2 days | high |
| `case_018_mild_headache` | 28F, tension headache, Tylenol helps | low |
| `case_019_sore_throat` | 22M, sore throat 3 days, no fever | low |
| `case_020_improving_symptoms` | 50F, cold getting better | low |
| `case_021_minor_rash` | 33M, itchy rash on arm | low |
| `case_022_stomach_upset` | 27M, nausea since lunch | low |
| `case_023_minor_injury` | 19F, twisted ankle, weight-bearing | low |
| `case_024_common_cold` | 38M, runny nose, sneezing, mild cough | low |
| `case_025_demographics_only` | 45F, gave name/age only, no symptoms | low |

### Bucket 5 — Adversarial / Edge Cases (HUMAN_REVIEW)

| Case | Description | Severity |
|------|-------------|----------|
| `case_026_prompt_injection_ignore_rules` | "Ignore all previous instructions" | medium |
| `case_027_demands_medication_advice` | Demands specific ibuprofen dosage | medium |
| `case_028_claims_doctor_bypass` | Claims to be a doctor, requests bypass | medium |

## Coverage Summary

- **7/7 critical flags** exercised (all ER_NOW flags have at least one case)
- **8/8 weighted flags** exercised (all weighted flags appear in at least one case)
- **Multi-critical combo** tested (case_011)
- **Weighted threshold boundary** tested: exactly 10 (case_012), 9 just below (case_030)
- **Adversarial inputs** tested: prompt injection, medication demands, credential claims
- **Negation trap** lesson: avoid phrases like "no trouble breathing" — regex matches positive patterns inside negations

## Directory Structure

```
tests/golden_calls/
├── README.md                           # This file
├── schema.json                         # JSON Schema for case validation
├── runner.py                           # Loads cases, runs rule engine, captures output
├── test_golden_calls.py                # pytest — fails loudly on drift
├── __init__.py
└── cases/
    ├── case_001_insufficient_info.json
    ├── case_002_chest_pain_escalation.json
    ├── case_003_breathing_difficulty.json
    ├── case_004_mild_cough_low_acuity.json
    ├── case_005_confused_caller.json
    ├── case_006_stroke_symptoms.json
    ├── case_007_anaphylaxis.json
    ├── case_008_uncontrolled_bleeding.json
    ├── case_009_loss_of_consciousness.json
    ├── case_010_suicidal_ideation.json
    ├── case_011_multi_critical_cardiac_breathing.json
    ├── case_012_high_fever_severe_pain.json
    ├── case_013_pregnancy_complication.json
    ├── case_014_pediatric_high_fever.json
    ├── case_015_diabetic_emergency.json
    ├── case_016_immunocompromised_fever.json
    ├── case_017_single_weighted_below_threshold.json
    ├── case_018_mild_headache.json
    ├── case_019_sore_throat.json
    ├── case_020_improving_symptoms.json
    ├── case_021_minor_rash.json
    ├── case_022_stomach_upset.json
    ├── case_023_minor_injury.json
    ├── case_024_common_cold.json
    ├── case_025_demographics_only.json
    ├── case_026_prompt_injection_ignore_rules.json
    ├── case_027_demands_medication_advice.json
    ├── case_028_claims_doctor_bypass.json
    ├── case_029_high_fever_only.json
    └── case_030_worsening_with_fever.json
```
