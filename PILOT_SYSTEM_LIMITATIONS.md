# System Limitations — Nurse Triage Assistant
<!-- Purpose: Clearly define what the system does and does NOT do for pilot participants -->
<!-- Date Created: 2026-03-02 -->
<!-- Task: 5B — System Limitations Documentation -->

## What the System Does

- Conducts structured patient intake via phone call (Twilio Voice)
- Collects: patient name, age, sex, chief complaint, symptom details
- Runs deterministic safety rules to detect high-risk patterns
- Produces a triage disposition recommendation (urgency level)
- Generates SBAR-formatted handoff summaries for clinical staff
- Provides caller with safety-net instructions
- Stores session data, transcripts, and decision traces for audit

## What the System Does NOT Do

1. **Does not diagnose conditions.** It triages urgency, not illness.
2. **Does not prescribe or recommend medications.** It never tells callers what to take.
3. **Does not replace nurse or clinician judgment.** Every output is a recommendation for human review.
4. **Does not make autonomous clinical decisions.** A human clinician must confirm or override every disposition.
5. **Does not provide medical advice.** It collects information and produces structured summaries.
6. **Does not guarantee accuracy.** The system uses pattern matching and AI language models, both of which can err.
7. **Does not learn from individual calls.** The model does not fine-tune on patient data. Updates are manual and version-controlled.
8. **Does not handle non-English callers.** Language barrier cases should be routed to human triage directly.
9. **Does not store or transmit unencrypted PHI.** All data in transit and at rest is encrypted.
10. **Does not send patient data to monitoring/error tracking tools.** Sentry events are scrubbed of PHI.

## Do Not Use This System For

- **Confirmed emergencies in progress** — Callers who state they are having an active emergency should be instructed to call 911 directly.
- **Situations requiring real-time clinical intervention** — The system cannot administer treatment or guide procedures.
- **Patients who require translator services** — The system operates in English only.
- **Legal, insurance, or billing inquiries** — The system handles clinical triage only.

## Safety Architecture

```
Caller Input
  ↓
Deterministic Red-Flag Check ← Cannot be overridden. Runs first. Catches critical patterns.
  ↓
Protocol Rules ← Version-controlled clinical protocols. Override LLM.
  ↓
LLM Assessment ← Supplementary. Can be overridden by safety gate.
  ↓
Safety Gate ← Final check. Forces HUMAN_REVIEW on anomalies.
  ↓
Disposition Output
```

**Design principle:** The system over-triages rather than under-triages. When uncertain, it escalates to a human.

## Known Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| Red-flag detection is regex-based | May miss novel or unusual phrasing of danger signals | LLM provides secondary safety check; HUMAN_REVIEW default on uncertainty |
| LLM can hallucinate | May produce inconsistent or incorrect assessments | Deterministic rules override LLM; safety gate validates output schema |
| Confused/incoherent caller detection is heuristic | Edge cases may not trigger altered-mental-status flag | Conservative default: any uncertainty → HUMAN_REVIEW |
| No load testing completed | System behavior under high concurrent call volume is unvalidated | Pilot volume expected to be low; load testing planned pre-scale |
| Caller minimizing symptoms | Caller who downplays real danger may be under-triaged | Safety-net instructions always provided; pilot review catches discrepancies |
| Audio quality affects STT accuracy | Poor connection or background noise degrades transcript | Partial transcripts still processed; missing info → HUMAN_REVIEW |

## Human Responsibility Statement

> **The Nurse Triage Assistant is a decision-support tool. The human clinician retains full responsibility for all clinical decisions. The system's output is a recommendation, not a directive. No patient care action should be taken based solely on the system's disposition without clinician review and confirmation.**
