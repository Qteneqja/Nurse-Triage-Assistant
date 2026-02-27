# Golden Call Dataset Specification

## Overview

The Golden Call dataset is a collection of structured, synthetic medical scenarios used to validate the Nurse Triage Assistant's behavior end-to-end. Each case represents a realistic phone intake conversation with known outcomes.

---

## Data Format

### Top-Level Structure

```json
{
  "case_id": "GC_001",
  "title": "Severe Chest Pain with Cardiac Risk Factors",
  "metadata": {
    "category": "life_threatening",
    "complaint_type": "chest_pain",
    "expected_disposition": "ER_NOW",
    "expected_red_flags": ["CHEST_PAIN_SEVERE"],
    "author": "Clinical Safety Team",
    "created_date": "2026-02-22",
    "version": "1.0"
  },
  "caller_profile": {
    "name": "John Smith",
    "age": 58,
    "sex": "male",
    "context": "History of hypertension and smoking"
  },
  "conversation": [
    {
      "turn": 1,
      "speaker": "assistant",
      "text": "Hello, I'm a nurse triage assistant. I'm here to help assess your condition and make sure you get the right care. Can I have your name please?"
    },
    {
      "turn": 2,
      "speaker": "caller",
      "text": "My name is John"
    },
    {
      "turn": 3,
      "speaker": "assistant",
      "text": "Nice to meet you, John. How old are you?"
    },
    ...
  ],
  "expected_outcomes": {
    "disposition": "ER_NOW",
    "triggers_911": true,
    "sbar_structure": {
      "situation": "Patient describes crushing chest pain radiating to left arm",
      "background": "58-year-old male with history of hypertension and smoking",
      "assessment": "Acute cardiac chest pain with red-flag features; immediate emergency care required",
      "recommendation": "Call 911 immediately; transfer to nearest ER for cardiac evaluation"
    },
    "min_turns_to_escalate": 3,
    "max_turns_before_escalate": 5
  },
  "validation": {
    "must_trigger_red_flags": ["CHEST_PAIN_SEVERE"],
    "must_not_proceed_to_full_intake": true,
    "must_have_sbar": true,
    "must_explain_escalation": true
  }
}
```

---

## Field Definitions

### Metadata
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `case_id` | string | ✓ | Unique identifier (format: `GC_XXX` where XXX is 001-999) |
| `title` | string | ✓ | Human-readable case title |
| `category` | enum | ✓ | One of: `life_threatening`, `urgent`, `moderate`, `mild`, `edge_case` |
| `complaint_type` | string | ✓ | Medical complaint (e.g., `chest_pain`, `breathing_difficulty`, `fever`) |
| `expected_disposition` | enum | ✓ | Expected final disposition: `ER_NOW`, `URGENT`, `SCHEDULE`, `SELF_CARE`, `HUMAN_REVIEW` |
| `expected_red_flags` | array of strings | ✓ | Rule IDs expected to trigger (e.g., `["CHEST_PAIN_SEVERE"]`) |
| `author` | string | ✗ | Person/team who authored the case |
| `created_date` | ISO 8601 date | ✗ | Creation date (e.g., `2026-02-22`) |
| `version` | string | ✗ | Case version (default: `1.0`) |

### Caller Profile
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✓ | Patient's name (can be fake if synthetic) |
| `age` | integer | ✓ | Patient's age in years (0–120) |
| `sex` | enum | ✓ | One of: `male`, `female`, `unknown` |
| `context` | string | ✗ | Additional context (e.g., medical history, social factors) |

### Conversation
Array of turn objects, each with:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `turn` | integer | ✓ | Turn number (1, 2, 3, ...) |
| `speaker` | enum | ✓ | One of: `assistant`, `caller` (system prompts are implicit) |
| `text` | string | ✓ | Spoken utterance (as heard by STT or typed by human) |
| `metadata` | object | ✗ | Optional: STT confidence, pause duration, interruptions, etc. |

### Expected Outcomes
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `disposition` | enum | ✓ | Final disposition |
| `triggers_911` | boolean | ✗ | True if 911 should be called (subset of ER_NOW) |
| `sbar_structure` | object | ✓ | Expected SBAR fields (Situation, Background, Assessment, Recommendation) |
| `min_turns_to_escalate` | integer | ✗ | Minimum turns before escalation (for red-flag cases) |
| `max_turns_before_escalate` | integer | ✗ | Maximum safety turns before mandatory escalation |

### Validation Rules
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `must_trigger_red_flags` | array | ✓ | Red flag rule IDs that **must** trigger |
| `must_not_proceed_to_full_intake` | boolean | ✓ | If true, system should escalate quickly (ER_NOW/URGENT) |
| `must_have_sbar` | boolean | ✓ | If true, escalation must include SBAR |
| `must_explain_escalation` | boolean | ✓ | If true, caller must hear reason for escalation |
| `assistant_must_not` | array | ✗ | Forbidden behaviors (e.g., `["diagnose", "give_medication"]`) |

---

## Case Categories

### 1. Life-Threatening (5 cases)
- **Purpose:** Verify immediate red-flag escalation
- **Characteristics:**
  - Deterministic red flags trigger (CHEST_PAIN_SEVERE, BREATHING_FAILURE, STROKE_SIGNS, ANAPHYLAXIS, SUICIDAL)
  - Escalation happens within 1–3 turns
  - Minimal intake (name, age, chief complaint only)
  - Short SBAR generated on escalation
  - 911 instruction is primary output
- **Example:** Crushing chest pain, facial droop, inability to breathe, throat closing

### 2. Urgent (5 cases)
- **Purpose:** Verify urgent escalation with some intake
- **Characteristics:**
  - Red flags trigger (SEVERE_ALLERGIC_REACTION, UNCONTROLLED_BLEEDING, PEDIATRIC_EMERGENT)
  - Escalation within 3–5 turns
  - Partial intake (name, age, complaint, onset, severity)
  - SBAR includes basic symptom details
  - ER/urgent care directive given
- **Example:** Severe allergic reaction with moderate airway compromise, uncontrolled bleeding

### 3. Moderate (5 cases)
- **Purpose:** Verify full intake + SBAR before escalation to nurse
- **Characteristics:**
  - **No immediate red flags**
  - Caller may ask for nurse partway through
  - Assistant validates request, explains AI value, continues intake
  - 8–10 turns (typical intake conversation)
  - Full intake collected (complaint, onset, severity, meds, allergies, PMH, etc.)
  - SBAR generated with complete details
  - Escalation to nurse handoff with SBAR
- **Example:** Moderate flu symptoms, ongoing abdominal pain, minor injury

### 4. Mild (5 cases)
- **Purpose:** Verify self-care guidance and safe discharge
- **Characteristics:**
  - No red flags, mild symptoms
  - Full intake completed (5–8 turns)
  - Disposition: SELF_CARE or SCHEDULE
  - Safety-net instructions given
  - Patient summary provided
- **Example:** Minor cough, mild rash, common cold, non-emergency injury

### 5. Edge Cases (5 cases)
- **Purpose:** Test boundary conditions and unusual scenarios
- **Examples:**
  1. **Contradictory Information:** "I'm not in pain" then "I have severe pain"
  2. **Incoherent Caller:** Long rambling answers, off-topic
  3. **Escalation Insistence:** Caller demands nurse after 2 turns
  4. **Refusal to Answer:** "I don't want to answer that"
  5. **LLM Limitation Trigger:** Case that exposes LLM hallucination risk

---

## Authoring Guidelines

### For New Cases

1. **Start with a Real Medical Scenario** (anonymized or synthetic)
   - Use clinical knowledge or consult medical literature
   - Ensure plausibility for the complaint type

2. **Map to Expected Behavior**
   - Identify which red flags should trigger (if any)
   - Estimate number of turns
   - Draft expected SBAR

3. **Write Caller Dialogue**
   - Use realistic speech patterns
   - Include natural hesitations, clarifications
   - Simulate speech-to-text variations (e.g., homophones: "know" vs. "no")

4. **Validate Against Schema**
   - Ensure all required fields are present
   - Use correct enum values
   - Validate case_id uniqueness

5. **Test with Orchestrator**
   - Run through mock orchestrator
   - Verify expected outcomes match observed behavior
   - Document any mismatches

### Template for New Cases

```json
{
  "case_id": "GC_0XX",
  "title": "[Complaint Type]: [Distinguishing Feature]",
  "metadata": {
    "category": "[life_threatening|urgent|moderate|mild|edge_case]",
    "complaint_type": "[specific complaint]",
    "expected_disposition": "[ER_NOW|URGENT|SCHEDULE|SELF_CARE|HUMAN_REVIEW]",
    "expected_red_flags": [],
    "author": "[your name]",
    "created_date": "2026-02-22",
    "version": "1.0"
  },
  "caller_profile": {
    "name": "[name]",
    "age": [age],
    "sex": "[male|female|unknown]",
    "context": "[optional medical/social context]"
  },
  "conversation": [
    {"turn": 1, "speaker": "assistant", "text": "[opening]"},
    {"turn": 2, "speaker": "caller", "text": "[response]"},
    ...
  ],
  "expected_outcomes": {
    "disposition": "[ER_NOW|URGENT|SCHEDULE|SELF_CARE|HUMAN_REVIEW]",
    "triggers_911": [true|false],
    "sbar_structure": {
      "situation": "[caller's complaint in clinical terms]",
      "background": "[age, risk factors, relevant history]",
      "assessment": "[synthesis of red flags, severity, urgency]",
      "recommendation": "[care pathway and next steps]"
    },
    "min_turns_to_escalate": [number],
    "max_turns_before_escalate": [number]
  },
  "validation": {
    "must_trigger_red_flags": [],
    "must_not_proceed_to_full_intake": [true|false],
    "must_have_sbar": true,
    "must_explain_escalation": true,
    "assistant_must_not": []
  }
}
```

---

## Validation Checklist for New Cases

- [ ] `case_id` is unique and follows `GC_XXX` format
- [ ] All required metadata fields are present
- [ ] Caller profile has valid age (0–120) and sex
- [ ] Conversation has alternating assistant/caller turns (starting with assistant)
- [ ] All turn objects have required fields (`turn`, `speaker`, `text`)
- [ ] `expected_disposition` matches actual red-flag logic
- [ ] `expected_red_flags` are actual rule IDs (e.g., `CHEST_PAIN_SEVERE`)
- [ ] SBAR structure has Situation, Background, Assessment, Recommendation
- [ ] `min_turns_to_escalate` ≤ `max_turns_before_escalate` (if both provided)
- [ ] `must_trigger_red_flags` matches `expected_disposition` (e.g., ER_NOW → critical flags)
- [ ] Case category makes sense (e.g., `life_threatening` has ER_NOW disposition)
- [ ] Dialogue is realistic and natural
- [ ] No real PHI (use fake names, generic ages where appropriate)

---

## Testing Strategy for Golden Calls

### Load & Parse
```python
def load_golden_call(filepath: str) -> dict:
    with open(filepath) as f:
        case = json.load(f)
    validate_case_schema(case)  # Schema validation
    return case
```

### Replay
```python
async def replay_golden_call(case: dict, orchestrator: Orchestrator) -> Result:
    session = create_session()
    for turn in case["conversation"]:
        if turn["speaker"] == "caller":
            result = await orchestrator.process_turn(session, turn["text"])
            session.update(result)
    return session.finalize()
```

### Assert
```python
def validate_result(result: Result, case: dict) -> bool:
    assert result.disposition == case["expected_outcomes"]["disposition"]
    assert all(flag in result.red_flags for flag in case["expected_red_flags"])
    assert result.sbar is not None  # SBAR should exist
    return True
```

---

## File Organization

```
tests/golden_calls/
├── 001_life_threatening/
│   ├── GC_001_chest_pain_severe.json
│   ├── GC_002_breathing_failure.json
│   ├── GC_003_stroke_signs.json
│   ├── GC_004_anaphylaxis.json
│   └── GC_005_suicidal.json
├── 002_urgent/
│   ├── GC_006_allergic_reaction.json
│   ├── GC_007_uncontrolled_bleeding.json
│   ├── GC_008_pediatric_fever.json
│   ├── GC_009_loss_of_consciousness.json
│   └── GC_010_cardiac_risk.json
├── 003_moderate/
│   ├── GC_011_moderate_flu.json
│   ├── GC_012_abdominal_pain.json
│   ├── GC_013_injury_with_nurse_request.json
│   ├── GC_014_difficult_diagnosis.json
│   └── GC_015_partial_refusal.json
├── 004_mild/
│   ├── GC_016_common_cold.json
│   ├── GC_017_mild_rash.json
│   ├── GC_018_minor_injury.json
│   ├── GC_019_mild_headache.json
│   └── GC_020_anxiety.json
├── 005_edge_cases/
│   ├── GC_021_contradictory_info.json
│   ├── GC_022_incoherent_caller.json
│   ├── GC_023_escalation_insistence.json
│   ├── GC_024_refusal_to_answer.json
│   └── GC_025_hallucination_risk.json
└── _schema.json  # JSON Schema definition for validation
```

---

## Expected Outcomes Specification

### Disposition Enum
- **ER_NOW:** Immediate 911/ER; critical signs present
- **URGENT:** ER/urgent care within 1–2 hours; concerning but stable
- **SCHEDULE:** PCP appointment within days; non-emergency but needs evaluation
- **SELF_CARE:** Home management; safety-net instructions provided
- **HUMAN_REVIEW:** Insufficient data; escalate to nurse for clarification

### SBAR Components
- **Situation:** Chief complaint and acute presentation (caller's words + severity)
- **Background:** Age, relevant medical history, risk factors, current meds, allergies
- **Assessment:** Synthesis of red flags, pattern recognition, urgency level
- **Recommendation:** Next care step (911, ER, PCP, self-care, nurse review)

---

## Known Limitations & Future Enhancements

1. **Speech-to-Text Variations:** Cases assume perfect transcription; future versions will include STT confidence levels and homophones (e.g., "know" / "no")
2. **Accent & Regional Variations:** All cases use standardized English; non-English speaker cases are Phase 5+
3. **Emotional State:** Cases don't model anxiety, panic, or emotional distress responses; pure symptom-focused
4. **Multi-Language:** Currently English-only; Spanish/Mandarin support is future
5. **Real Patient Data:** Cases are synthetic; validation against real EMR data is future compliance work

