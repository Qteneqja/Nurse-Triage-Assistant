# Pilot Success Metrics (2–4 Week Pilot)
<!-- Purpose: Define measurable success criteria for the pilot period -->
<!-- Date Created: 2026-03-02 -->
<!-- Task: 7 — Pilot Success Metrics -->

## Primary Safety Metrics (Must Pass — Pilot Continues Only If Met)

| # | Metric | Definition | Target | How to Measure | Fail Threshold |
|---|--------|-----------|--------|---------------|----------------|
| 1 | **Under-triage rate** | % of calls where nurse determined a higher urgency than system disposition | < 5% | Nurse agree/disagree feedback per call | > 10% → pause pilot |
| 2 | **Critical miss rate** | % of ER_NOW-worthy calls where system gave lower disposition | 0% | Nurse records "under-triaged + should have been ER_NOW" | > 0% → stop pilot, investigate |
| 3 | **Red flag detection rate** | % of calls with known critical symptoms where deterministic red flags fired | 100% | Cross-reference nurse-identified critical symptoms vs. `red_flags_triggered` in decision trace | < 95% → investigate |
| 4 | **System availability** | % uptime of `/health` endpoint during pilot hours | > 99% | Azure Container Apps health probe logs | < 95% → pause pilot |

## Secondary Quality Metrics (Track — Do Not Gate Pilot)

| # | Metric | Definition | Target | How to Measure |
|---|--------|-----------|--------|---------------|
| 5 | **Over-triage rate** | % of calls where nurse determined a lower urgency than system | < 30% | Nurse agree/disagree feedback |
| 6 | **Nurse agreement rate** | % of calls where nurse confirms system disposition is appropriate | > 70% | Nurse feedback: "agree" / total calls |
| 7 | **Call completion rate** | % of calls where caller completes full intake flow (not abandoned) | > 75% | Database: sessions with disposition vs. total sessions started |
| 8 | **HUMAN_REVIEW rate** | % of all calls receiving HUMAN_REVIEW disposition | Monitor (no target) | Database: `disposition = HUMAN_REVIEW` / total |

> A high HUMAN_REVIEW rate (>50%) may indicate the system is too conservative for practical use. Track and discuss at weekly review.

## Operational Metrics (Monitor — Inform Engineering)

| # | Metric | Definition | Source | Alert Threshold |
|---|--------|-----------|--------|----------------|
| 9 | **LLM latency (p50 / p95)** | Median and 95th percentile LLM response time | Application logs / Sentry | p95 > 15s → investigate |
| 10 | **JSON validation failure rate** | % of LLM responses failing Pydantic schema validation | Sentry events | > 5% → investigate |
| 11 | **LLM timeout rate** | % of LLM calls exceeding timeout threshold | Sentry events | > 3% → investigate |
| 12 | **Twilio signature rejection count** | Number of rejected webhook requests (invalid/missing signature) | Application logs / Sentry | > 10/hour → investigate (possible attack) |
| 13 | **Database error rate** | Failed DB operations / total operations | Sentry events | Any → investigate |
| 14 | **Mean turns per call** | Average number of dynamic conversation turns before disposition | Database: count turns per session | If > 10 average → review prompt efficiency |

## Data Collection Method

### Per-Call Nurse Feedback (Required During Pilot)
After reviewing each SBAR, the nurse records:

| Field | Options |
|-------|---------|
| **Agreement** | Agree / Disagree — under-triaged / Disagree — over-triaged |
| **What would you have done differently?** | Free text (optional) |
| **Were safety-net instructions appropriate?** | Yes / No / Partially |

> **Collection mechanism:** During pilot, use a shared spreadsheet or form linked to `session_id`. Dashboard collection planned for Phase 8.

### Weekly Review Meeting
- Review all metrics above
- Discuss every "disagree — under-triaged" case in detail
- Any under-triage case becomes a candidate golden-call regression test
- Adjust red-flag patterns if false negatives found
- Document decisions and rationale

## Pilot Decision Framework

| Outcome | Action |
|---------|--------|
| All primary metrics pass, secondary trending well | Expand pilot (more call volume or more hours) |
| All primary metrics pass, secondary metrics weak | Continue pilot, adjust prompts/rules, re-evaluate in 1 week |
| Any primary metric fails (except critical miss) | Pause pilot, investigate root cause, fix, resume |
| Critical miss (ER_NOW-worthy call under-triaged) | **Stop pilot immediately.** Full incident review. Fix rule engine. Add golden-call test case. Resume only after clinical lead approval. |
