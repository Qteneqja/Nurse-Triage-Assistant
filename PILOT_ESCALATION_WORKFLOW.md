# Pilot Escalation Workflow
<!-- Purpose: Define how the system escalates cases to human clinicians during pilot -->
<!-- Date Created: 2026-03-02 -->
<!-- Task: 5A — Escalation Workflow Documentation -->

## Overview

The Nurse Triage Assistant is a **decision-support tool**. During the pilot, **every disposition triggers nurse awareness** — the system never makes final clinical decisions autonomously.

---

## When the System Escalates

| Disposition | Trigger | Urgency | Action Required |
|-------------|---------|---------|----------------|
| **ER_NOW** | Critical red flag detected (cardiac, breathing failure, stroke, anaphylaxis, uncontrolled bleeding, loss of consciousness, suicidal ideation) | **Immediate** | Caller advised to call 911. Nurse notified immediately. |
| **URGENT** | Multiple weighted red flags with combined score ≥ threshold | **< 15 min** | Nurse callback within 15 minutes |
| **HUMAN_REVIEW** | Insufficient information for safe triage, or uncertainty in assessment | **< 30 min** | Nurse reviews SBAR and calls patient back |
| **PCP** | Non-urgent symptoms, schedule with primary care | **< 24 hours** | Added to scheduling queue |
| **SAFE / SELF_CARE** | Low-acuity, self-care instructions appropriate | **Informational** | Safety-net instructions provided. Nurse reviews SBAR. |

> **Pilot override:** During the pilot period, ALL dispositions (including SAFE/SELF_CARE) are reviewed by a nurse within 24 hours. No disposition is considered final without human confirmation.

---

## Who Receives Escalations

| Role | Receives | Response Time |
|------|----------|---------------|
| **On-Duty Triage Nurse** | ER_NOW, URGENT | Immediate / < 15 min |
| **Triage Queue Nurse** | HUMAN_REVIEW, PCP | < 30 min / < 24 hours |
| **Pilot Supervisor** | All dispositions (daily summary) | End of shift |
| **Clinical Lead** | Disagreements, incidents, safety events | As escalated |

> **TODO:** Assign specific individuals/roles before pilot launch.

---

## What Information Is Provided to the Nurse

Each escalation includes a **SBAR Handoff Report** containing:

1. **S (Situation):** Patient name, age, sex, chief complaint, onset
2. **B (Background):** Medical history, medications, allergies (as reported by caller)
3. **A (Assessment):** System's analysis of symptoms, red flags triggered, confidence level
4. **R (Recommendation):** Suggested disposition with rationale

Plus structured metadata:
- `session_id` — unique call identifier
- `disposition` — system recommendation (e.g., HUMAN_REVIEW)
- `red_flags_triggered` — list of deterministic safety flags that fired
- `rules_triggered` — list of rule IDs
- `confidence` — system confidence score (0.0–1.0)
- `timestamp` — when the call was processed
- `call_sid` — Twilio call identifier (if via phone)
- `safety_net_instructions` — instructions given to the caller

---

## Handoff Process

```
1. Caller calls Twilio number
   ↓
2. System conducts structured intake (name, age, sex, chief complaint)
   ↓
3. Dynamic symptom assessment (up to 12 turns)
   ↓
4. Deterministic red-flag engine runs (BEFORE LLM)
   → If critical flag: immediate ER_NOW disposition, caller advised to call 911
   ↓
5. LLM assessment + safety gate check
   ↓
6. Disposition assigned + SBAR generated
   ↓
7. SBAR report stored (database + blob storage)
   ↓
8. Nurse receives notification
   → ER_NOW: Immediate alert
   → URGENT: Priority queue
   → HUMAN_REVIEW: Standard queue
   ↓
9. Nurse reviews SBAR in dashboard/system
   ↓
10. Nurse contacts patient
   ↓
11. Nurse records outcome:
    - Agree with disposition
    - Disagree — under-triaged (system said lower, nurse says higher)
    - Disagree — over-triaged (system said higher, nurse says lower)
    - Notes
   ↓
12. Session marked as resolved
```

> **Pilot note:** Step 8 notification mechanism is currently file/database-based. Dashboard implementation is planned for Phase 8.

---

## Fallbacks

| Scenario | Fallback |
|----------|----------|
| System is down (`/ready` fails) | Route all calls to nurse queue directly. No system triage. |
| LLM API is unreachable | System defaults to HUMAN_REVIEW for all calls. Safety-net instructions still provided. |
| Database is down | System cannot store sessions. Calls should route to manual triage. |
| Caller hangs up mid-intake | Partial session saved. Nurse reviews whatever information was captured. |
| System produces unexpected output | Safety gate forces HUMAN_REVIEW. Sentry alert fires. |

> **TODO:** Define the phone routing fallback (e.g., Twilio Studio flow that routes to nurse queue when `/ready` returns 503).

---

## Logging and Audit

Every call produces an audit trail:

| Event | Logged | Location |
|-------|--------|----------|
| Call start | ✅ | Database session table |
| Each intake turn | ✅ | Database transcript |
| Red flag triggered | ✅ | Database decision trace, Sentry tag |
| LLM call made | ✅ | Database, application logs |
| Safety gate override | ✅ | Database decision trace, Sentry breadcrumb |
| Disposition assigned | ✅ | Database decision trace |
| SBAR generated | ✅ | Database + blob storage |
| Nurse review outcome | ⬜ Planned | Dashboard (Phase 8) |

---

## Feedback Loop (Pilot Quality Improvement)

1. Nurse records agree/disagree for each disposition
2. Every **disagree — under-triaged** case is reviewed by the clinical lead
3. Under-triage cases become **new golden-call regression tests** to prevent recurrence
4. Over-triage cases are tracked but are **acceptable** — the system is designed to err toward safety
5. Weekly pilot review meeting: metrics, nurse feedback, golden-call updates
