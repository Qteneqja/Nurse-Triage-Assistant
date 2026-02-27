"""
Deterministic Red-Flag Engine

Conservative, pattern-based safety rules that run BEFORE any LLM call.
These rules always win — the LLM safety agent is additive only.

Design principles:
- False positives are acceptable (conservative)
- False negatives are NOT acceptable for life-threatening patterns
- Rules check both the raw caller utterance AND accumulated structured state
- Each rule returns a voice-friendly escalation script

Phase 1 additions:
- Structured RedFlagDefinition with weight/critical fields
- score_red_flags(): returns (disposition, total_score, triggered_ids)
  CRITICAL flag  → ER_NOW  (regardless of score)
  score ≥ 10     → URGENT
  else           → UNDECIDED
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from src.orchestrator.schemas import RedFlagResult, SafetyLevel


# ---------------------------------------------------------------------------
# Phase 1: Structured Red Flag Definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RedFlagDefinition:
    """A single, explicitly-defined red flag for Phase 1 scoring.

    Attributes:
        id: Unique machine-readable identifier.
        description: Human-readable description for audit logs.
        weight: Additive score contribution (used when critical=False).
        critical: If True, immediately forces ER_NOW disposition.
        recommended_disposition: The disposition this flag alone recommends.
        patterns: Optional regex patterns for utterance matching.
    """
    id: str
    description: str
    recommended_disposition: str  # "ER_NOW" | "URGENT" | "UNDECIDED"
    weight: int = 0
    critical: bool = False
    patterns: tuple = field(default_factory=tuple)


# ER_NOW critical flags (weight irrelevant — any match → ER_NOW immediately)
_PHASE1_CRITICAL_FLAGS: List[RedFlagDefinition] = [
    RedFlagDefinition(
        id="rf_cardiac_arrest_signs",
        description="Signs consistent with cardiac arrest or severe cardiac event",
        critical=True,
        weight=0,
        recommended_disposition="ER_NOW",
        patterns=(
            r"chest\s+pain.*(crush|press|squeez|elephant|radiati)",
            r"(crush|press|squeez|elephant).*(chest|heart)",
            r"heart\s+attack",
            r"chest\s+pain.*(arm|jaw|neck|back|shoulder)",
            r"chest\s+pain.*(sweat|nausea|dizz|faint|short.?of.?breath)",
            r"(severe|worst|10.?out.?of|unbearable).*(chest\s+pain)",
        ),
    ),
    RedFlagDefinition(
        id="rf_severe_breathing_failure",
        description="Severe acute breathing difficulty indicating airway compromise",
        critical=True,
        weight=0,
        recommended_disposition="ER_NOW",
        patterns=(
            r"can\s*'?\s*t\s+breath",
            r"cannot\s+breath",
            r"trouble\s+breathing",
            r"difficulty\s+breathing",
            r"struggling\s+to\s+breath",
            r"gasping",
            r"choking",
            r"can\s*'?\s*t\s+get\s+(enough\s+)?air",
            r"short(ness)?\s+of\s+breath.*(severe|very|extreme|bad|worst)",
            r"(severe|very|extreme|bad|worst).*(short(ness)?\s+of\s+breath|breathing)",
            r"lips?\s+(turning\s+)?blue",
            r"turning\s+blue",
        ),
    ),
    RedFlagDefinition(
        id="rf_stroke_signs",
        description="Classic FAST stroke symptoms",
        critical=True,
        weight=0,
        recommended_disposition="ER_NOW",
        patterns=(
            r"face\s+(is\s+)?(droop|numb|tingl)",
            r"(arm|leg)\s+(is\s+)?(weak|numb|can\s*'?\s*t\s+move|tingl)",
            r"(can\s*'?\s*t|cannot|unable\s+to)\s+(speak|talk|form\s+words)",
            r"slurr(ing|ed)\s+(speech|words)",
            r"sudden.*(confus|vision\s+loss|headache.*worst)",
            r"(worst|thunderclap)\s+headache",
            r"stroke",
            r"one\s+side.*(weak|numb|drooping|not\s+working)",
        ),
    ),
    RedFlagDefinition(
        id="rf_anaphylaxis",
        description="Severe allergic reaction / anaphylaxis",
        critical=True,
        weight=0,
        recommended_disposition="ER_NOW",
        patterns=(
            r"(throat|tongue|lip).*(swell|clos|tight)",
            r"anaphyla",
            r"(can\s*'?\s*t|cannot)\s+(swallow|breath).*(swell|allerg)",
            r"(allerg|react).*(swell|hive|breath|throat)",
            r"epi\s*pen",
            r"severe\s+allerg",
        ),
    ),
    RedFlagDefinition(
        id="rf_uncontrolled_bleeding",
        description="Uncontrolled or arterial haemorrhage",
        critical=True,
        weight=0,
        recommended_disposition="ER_NOW",
        patterns=(
            r"(won\s*['']?\s*t|will\s+not|can\s*['']?\s*t|cannot)\s+stop\s+bleed",
            r"bleed.*(won\s*['']?\s*t|will\s+not|not)\s+stop",
            r"(heavy|severe|massive|uncontroll)\s+bleed",
            r"blood.*(everywhere|pouring|gushing|spurting|won\s*['']?\s*t\s+stop)",
            r"(pouring|gushing|spurting).*blood",
            r"soaked?\s+(through|in)(\s+\w+)*\s+blood",
            r"arterial\s+bleed",
        ),
    ),
    RedFlagDefinition(
        id="rf_loss_of_consciousness",
        description="Loss of consciousness or active seizure",
        critical=True,
        weight=0,
        recommended_disposition="ER_NOW",
        patterns=(
            r"(passed|faint|black)\s*out",
            r"(lost|losing)\s+consciousness",
            r"unresponsive",
            r"(won\s*'?\s*t|will\s+not|can\s*'?\s*t|cannot)\s+(wake|respond)",
            r"collaps(ed|ing)",
            r"seizur",
        ),
    ),
    RedFlagDefinition(
        id="rf_suicidal_self_harm",
        description="Suicidal ideation or active self-harm",
        critical=True,
        weight=0,
        recommended_disposition="ER_NOW",
        patterns=(
            r"(want|going|plan)\s+(to\s+)?(kill|hurt|harm|end)\s+(my\s*self|my\s+life|it\s+all)",
            r"suicid",
            r"self[\s-]?harm",
            r"don\s*'?\s*t\s+want\s+to\s+(live|be\s+alive|go\s+on|exist)",
            r"(better|easier)\s+(off\s+)?dead",
            r"(end|ending)\s+(it|my\s+life|everything)",
            r"overdos",
        ),
    ),
]

# Weighted (non-critical) flags — sum ≥ 10 → URGENT
_PHASE1_WEIGHTED_FLAGS: List[RedFlagDefinition] = [
    RedFlagDefinition(
        id="rf_high_fever",
        description="High fever (≥103°F / ≥39.4°C mentioned)",
        weight=5,
        recommended_disposition="URGENT",
        patterns=(
            r"fever.*(103|104|105|106|39\.[4-9]|4[0-2])",
            r"(103|104|105|106|39\.[4-9]|4[0-2]).*degree",
            r"very\s+high\s+fever",
            r"spike.*fever",
        ),
    ),
    RedFlagDefinition(
        id="rf_severe_pain",
        description="Severe pain (8-10/10 reported)",
        weight=5,
        recommended_disposition="URGENT",
        patterns=(
            r"(8|9|10)\s*(out\s+of\s+10|\/\s*10|\s*on\s+scale)",
            r"pain.*(8|9|10).*/\s*10",
            r"(worst|unbearable|excruciating|10[\s/]10)\s+pain",
        ),
    ),
    RedFlagDefinition(
        id="rf_altered_mental_status",
        description="Altered mental status / acute confusion",
        weight=6,
        recommended_disposition="URGENT",
        patterns=(
            r"(confused|disoriented|not\s+making\s+sense|incoherent)",
            # Require explicit orientation failure: not knowing WHERE/WHO they are
            # or what specific time-anchor (day/year/date) it is.
            # This prevents false-positives from colloquial phrases like
            # "I don't know what's going on" or "figure out what to do".
            r"(doesn\s*'?\s*t\s+know|don\s*'?\s*t\s+know)\s+"
            r"(where\s+(?:they|he|she|I)\s+|who\s+(?:they|he|she|I)\s+"
            r"|what\s+(?:day|year|date|month|time)\s+it\s+)",
            r"sudden\s+(confus|disorientation)",
        ),
    ),
    RedFlagDefinition(
        id="rf_pediatric_high_fever",
        description="High fever in infant/toddler (< 3 months with any fever)",
        weight=8,
        recommended_disposition="URGENT",
        patterns=(
            r"(infant|baby|newborn|3\s*month|2\s*month|1\s*month).*(fever|temp)",
            r"fever.*(infant|baby|newborn|3\s*month)",
        ),
    ),
    RedFlagDefinition(
        id="rf_pregnancy_complication",
        description="Pregnancy complication (bleeding, pain, no movement)",
        weight=7,
        recommended_disposition="URGENT",
        patterns=(
            r"pregnan.*(bleed|pain|no\s+movement|not\s+moving)",
            r"(bleed|cramp|contraction).*(pregnan|trimester)",
            r"(baby|fetus).*(not\s+moving|no\s+movement)",
        ),
    ),
    RedFlagDefinition(
        id="rf_diabetic_emergency",
        description="Diabetic emergency signs (very low/high blood sugar symptoms)",
        weight=6,
        recommended_disposition="URGENT",
        patterns=(
            r"blood\s+sugar.*(very\s+low|very\s+high|crash|spike)",
            r"(hypoglycemi|diabetic\s+keto|dka)",
            r"diabet.*(shaking|trembling|pass\s+out|unconscious)",
        ),
    ),
    RedFlagDefinition(
        id="rf_worsening_symptoms",
        description="Rapidly worsening symptoms",
        weight=4,
        recommended_disposition="URGENT",
        patterns=(
            r"(getting|getting\s+much|rapidly)\s+worse",
            r"(worsening|escalating|spreading)\s+(fast|quickly|rapidly)",
            r"symptom.*(spread|wors).*(hour|minute)",
        ),
    ),
    RedFlagDefinition(
        id="rf_immunocompromised_fever",
        description="Fever in immunocompromised patient",
        weight=7,
        recommended_disposition="URGENT",
        patterns=(
            r"(chemo|chemotherapy|immunosuppres|transplant|hiv|cancer).*(fever|temp|infect)",
            r"(fever|infect).*(chemo|immunosuppres|transplant|hiv|cancer)",
        ),
    ),
]

# Combined lookup
_ALL_PHASE1_FLAGS: List[RedFlagDefinition] = _PHASE1_CRITICAL_FLAGS + _PHASE1_WEIGHTED_FLAGS

# Fast pattern-to-flag index (compiled at module load)
_COMPILED_FLAGS: List[tuple] = []  # (RedFlagDefinition, List[re.Pattern])

for _rfd in _ALL_PHASE1_FLAGS:
    _pats = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _rfd.patterns]
    _COMPILED_FLAGS.append((_rfd, _pats))


def score_red_flags(
    utterance: str = "",
    chief_complaint: Optional[str] = None,
    red_flags_reported: Optional[List[str]] = None,
    symptom_severity: Optional[str] = None,
    confusion_score: float = 0.0,
) -> Tuple[str, int, List[str]]:
    """Phase 1 deterministic red-flag scoring.

    Evaluates pattern matches against the structured flag definitions.

    Logic (strict priority):
      1. Any CRITICAL flag hit → ("ER_NOW", score, ids)
      2. Total weight ≥ 10    → ("URGENT", score, ids)
      3. Otherwise            → ("UNDECIDED", score, ids)

    Args:
        utterance: Raw caller utterance for this turn.
        chief_complaint: Accumulated chief complaint from session state.
        red_flags_reported: Any LLM-extracted red flags.
        symptom_severity: Current severity string.
        confusion_score: 0–1 confusion score.

    Returns:
        Tuple of (disposition_str, total_score, list_of_triggered_flag_ids)

    Raises:
        Exception: Propagated upward so callers can fail-closed.
    """
    # Compose search text from all available signals
    texts: List[str] = []
    if utterance:
        texts.append(utterance)
    if chief_complaint:
        texts.append(chief_complaint)
    if red_flags_reported:
        texts.extend(red_flags_reported)
    combined_text = " ".join(texts).lower()

    triggered_ids: List[str] = []
    total_score: int = 0
    any_critical: bool = False

    for rfd, compiled_patterns in _COMPILED_FLAGS:
        matched = any(p.search(combined_text) for p in compiled_patterns)

        # State-only checks for flags without patterns
        if not matched and not rfd.patterns:
            if rfd.id == "rf_altered_mental_status" and confusion_score >= 0.8:
                matched = True
            elif rfd.id == "rf_severe_pain" and symptom_severity == "severe":
                matched = True

        if matched:
            triggered_ids.append(rfd.id)
            if rfd.critical:
                any_critical = True
            else:
                total_score += rfd.weight

    if any_critical:
        return ("ER_NOW", total_score, triggered_ids)

    if total_score >= 10:
        return ("URGENT", total_score, triggered_ids)

    return ("UNDECIDED", total_score, triggered_ids)


# ---------------------------------------------------------------------------
# Rule definitions: (name, utterance_patterns, state_check_fn, script, reason)
# ---------------------------------------------------------------------------

_UTTERANCE_RULES: List[dict] = [
    {
        "name": "severe_breathing_difficulty",
        "patterns": [
            r"can\s*'?\s*t\s+breath",
            r"cannot\s+breath",
            r"trouble\s+breathing",
            r"difficulty\s+breathing",
            r"struggling\s+to\s+breath",
            r"gasping",
            r"choking",
            r"can\s*'?\s*t\s+get\s+(enough\s+)?air",
            r"short(ness)?\s+of\s+breath.*(severe|very|extreme|bad|worst)",
            r"(severe|very|extreme|bad|worst).*(short(ness)?\s+of\s+breath|breathing)",
            r"lips?\s+(turning\s+)?blue",
            r"turning\s+blue",
        ],
        "script": (
            "I'm hearing that you may be having serious difficulty breathing. "
            "This could be a medical emergency. Please call 9 1 1 or have someone "
            "take you to the nearest emergency room right now. Do not wait."
        ),
        "reason": "Severe breathing difficulty pattern matched in caller utterance",
    },
    {
        "name": "severe_chest_pain",
        "patterns": [
            r"chest\s+pain.*(crush|press|squeez|elephant|radiati)",
            r"(crush|press|squeez|elephant).*(chest|heart)",
            r"heart\s+attack",
            r"chest\s+pain.*(arm|jaw|neck|back|shoulder)",
            r"chest\s+pain.*(sweat|nausea|dizz|faint|short.?of.?breath)",
            r"(severe|worst|10.?out.?of|unbearable).*(chest\s+pain)",
        ],
        "script": (
            "What you're describing sounds like it could be a cardiac emergency. "
            "Please call 9 1 1 immediately. Do not drive yourself. "
            "If you have aspirin available and are not allergic, chew one while you wait for help."
        ),
        "reason": "Severe chest pain with concerning qualifiers matched",
    },
    {
        "name": "stroke_symptoms",
        "patterns": [
            r"face\s+(is\s+)?(droop|numb|tingl)",
            r"(arm|leg)\s+(is\s+)?(weak|numb|can\s*'?\s*t\s+move|tingl)",
            r"(can\s*'?\s*t|cannot|unable\s+to)\s+(speak|talk|form\s+words)",
            r"slurr(ing|ed)\s+(speech|words)",
            r"sudden.*(confus|vision\s+loss|headache.*worst)",
            r"(worst|thunderclap)\s+headache",
            r"stroke",
            r"one\s+side.*(weak|numb|drooping|not\s+working)",
        ],
        "script": (
            "You may be experiencing stroke symptoms. This is a time-critical emergency. "
            "Please call 9 1 1 immediately. Note the time your symptoms started. "
            "Do not eat, drink, or take any medication until emergency services arrive."
        ),
        "reason": "Stroke-like symptom pattern matched",
    },
    {
        "name": "severe_allergic_reaction",
        "patterns": [
            r"(throat|tongue|lip).*(swell|clos|tight)",
            r"anaphyla",
            r"(can\s*'?\s*t|cannot)\s+(swallow|breath).*(swell|allerg)",
            r"(allerg|react).*(swell|hive|breath|throat)",
            r"epi\s*pen",
            r"severe\s+allerg",
        ],
        "script": (
            "This sounds like it could be a severe allergic reaction. "
            "If you have an EpiPen, use it now. Call 9 1 1 immediately. "
            "Do not wait to see if symptoms improve on their own."
        ),
        "reason": "Severe allergic reaction pattern matched",
    },
    {
        "name": "uncontrolled_bleeding",
        "patterns": [
            r"(won\s*['’]?\s*t|will\s+not|can\s*['’]?\s*t|cannot)\s+stop\s+bleed",
            r"bleed.*(won\s*['’]?\s*t|will\s+not|not)\s+stop",
            r"(heavy|severe|massive|uncontroll)\s+bleed",
            r"blood.*(everywhere|pouring|gushing|spurting|won\s*['’]?\s*t\s+stop)",
            r"(pouring|gushing|spurting).*blood",
            r"soaked?\s+(through|in)(\s+\w+)*\s+blood",
            r"arterial\s+bleed",
        ],
        "script": (
            "It sounds like you have significant bleeding that is not stopping. "
            "Apply firm, direct pressure to the wound with a clean cloth. "
            "Call 9 1 1 immediately. Do not remove the cloth — add more on top if needed."
        ),
        "reason": "Uncontrolled bleeding pattern matched",
    },
    {
        "name": "loss_of_consciousness",
        "patterns": [
            r"(passed|faint|black)\s*out",
            r"(lost|losing)\s+consciousness",
            r"unresponsive",
            r"(won\s*'?\s*t|will\s+not|can\s*'?\s*t|cannot)\s+(wake|respond)",
            r"collaps(ed|ing)",
            r"seizur",
        ],
        "script": (
            "Loss of consciousness or seizure activity can be a medical emergency. "
            "Please call 9 1 1 right away. If the person is on the ground, "
            "roll them onto their side and do not put anything in their mouth."
        ),
        "reason": "Loss of consciousness or seizure pattern matched",
    },
    {
        "name": "suicidal_self_harm",
        "patterns": [
            r"(want|going|plan)\s+(to\s+)?(kill|hurt|harm|end)\s+(my\s*self|my\s+life|it\s+all)",
            r"suicid",
            r"self[\s-]?harm",
            r"don\s*'?\s*t\s+want\s+to\s+(live|be\s+alive|go\s+on|exist)",
            r"(better|easier)\s+(off\s+)?dead",
            r"(end|ending)\s+(it|my\s+life|everything)",
            r"overdos",
        ],
        "script": (
            "I hear that you may be going through a very difficult time. "
            "Your safety is the most important thing right now. "
            "Please call or text 9 8 8, the Suicide and Crisis Lifeline, to speak with someone "
            "who can help. You can also call 9 1 1 or go to the nearest emergency room. "
            "You are not alone, and help is available right now."
        ),
        "reason": "Suicidal ideation or self-harm pattern matched — immediate human escalation required",
    },
]


def check_utterance(utterance: str) -> RedFlagResult:
    """Check a caller utterance against all deterministic red-flag rules.

    Args:
        utterance: Raw text from the caller (STT output).

    Returns:
        RedFlagResult with triggered=True if any rule matched.
        If multiple rules match, the first one's script is used (all are logged).
    """
    text = utterance.lower().strip()
    matched: List[str] = []
    first_script: str | None = None
    first_reason: str | None = None

    for rule in _UTTERANCE_RULES:
        for pattern in rule["patterns"]:
            if re.search(pattern, text):
                matched.append(rule["name"])
                if first_script is None:
                    first_script = rule["script"]
                    first_reason = rule["reason"]
                break  # one match per rule is enough

    if matched:
        return RedFlagResult(
            triggered=True,
            level=SafetyLevel.EMERGENT,
            script_to_say=first_script,
            reason_for_audit=first_reason,
            matched_rules=matched,
        )

    return RedFlagResult(triggered=False)


def check_state(
    chief_complaint: str | None = None,
    red_flags_reported: list[str] | None = None,
    symptom_severity: str | None = None,
    confusion_score: float = 0.0,
) -> RedFlagResult:
    """Check accumulated structured state fields for red-flag conditions.

    This is a secondary check over fields the LLM has extracted so far.

    Args:
        chief_complaint: The chief complaint text.
        red_flags_reported: List of red flags already reported.
        symptom_severity: Severity string.
        confusion_score: 0-1 score for confusion / poor historian.

    Returns:
        RedFlagResult.
    """
    matched: List[str] = []
    script: str | None = None
    reason: str | None = None

    # Check chief complaint for emergency keywords via utterance rules
    if chief_complaint:
        utt_result = check_utterance(chief_complaint)
        if utt_result.triggered:
            return utt_result

    # Extremely confused caller with severe symptoms
    if confusion_score >= 0.8 and symptom_severity in ("severe", "critical", "10"):
        matched.append("severe_confusion_with_severe_symptoms")
        script = (
            "Based on what I'm hearing, you may need immediate medical attention. "
            "Please call 9 1 1 or have someone take you to the emergency room right away."
        )
        reason = "High confusion score with severe symptoms"

    # Check LLM-reported red flags for known critical patterns
    if red_flags_reported:
        critical_keywords = [
            "chest pain", "breathing", "stroke", "bleeding",
            "unconscious", "seizure", "anaphylaxis", "suicid",
        ]
        for flag in red_flags_reported:
            flag_lower = flag.lower()
            for kw in critical_keywords:
                if kw in flag_lower:
                    matched.append(f"llm_reported_critical_flag:{flag}")
                    if script is None:
                        script = (
                            "Based on what you've told me, this may require emergency care. "
                            "Please call 9 1 1 or go to the nearest emergency room immediately."
                        )
                        reason = f"LLM-reported red flag contains critical keyword: {flag}"
                    break

    if matched:
        return RedFlagResult(
            triggered=True,
            level=SafetyLevel.EMERGENT,
            script_to_say=script,
            reason_for_audit=reason,
            matched_rules=matched,
        )

    return RedFlagResult(triggered=False)


def check_all(
    utterance: str,
    chief_complaint: str | None = None,
    red_flags_reported: list[str] | None = None,
    symptom_severity: str | None = None,
    confusion_score: float = 0.0,
) -> RedFlagResult:
    """Run all deterministic red-flag checks (utterance + state).

    Returns the first triggered result, or a non-triggered result.
    """
    # Utterance check first (most important for immediate danger signals)
    utt_result = check_utterance(utterance)
    if utt_result.triggered:
        return utt_result

    # State-based check
    state_result = check_state(
        chief_complaint=chief_complaint,
        red_flags_reported=red_flags_reported,
        symptom_severity=symptom_severity,
        confusion_score=confusion_score,
    )
    if state_result.triggered:
        return state_result

    return RedFlagResult(triggered=False)
