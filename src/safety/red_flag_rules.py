"""
Deterministic Red-Flag Rule Engine — Phase 1 Hardening

Structured rule registry with forced disposition overrides.
Runs BEFORE protocol logic. Runs BEFORE LLM output acceptance.
CANNOT be overridden by any downstream component.

Each rule has:
- Unique ID
- Condition (callable)
- Forced disposition
- Escalation script
- Weight (for scoring)
- Critical flag (bypasses scoring — instant override)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule context passed to condition lambdas
# ---------------------------------------------------------------------------
@dataclass
class RuleContext:
    """Input context for rule evaluation. Built from session state."""

    utterance: str = ""
    chief_complaint: str = ""
    red_flags_reported: List[str] = field(default_factory=list)
    symptom_severity: str = ""
    confusion_score: float = 0.0
    caller_age: Optional[int] = None
    pregnancy_status: Optional[str] = None

    @property
    def combined_text(self) -> str:
        parts = [self.utterance, self.chief_complaint]
        parts.extend(self.red_flags_reported)
        return " ".join(p for p in parts if p).lower()


# ---------------------------------------------------------------------------
# Rule definition
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RedFlagRule:
    """A single deterministic red-flag rule.

    Attributes:
        id: Unique rule identifier (e.g., 'CHEST_PAIN_SEVERE').
        description: Human-readable description for audit logs.
        condition: Callable(RuleContext) -> bool. Deterministic.
        forced_disposition: Disposition to force if triggered (ER_NOW/URGENT).
        escalation_script: Voice-friendly message to speak to caller.
        weight: Integer weight for scoring (used when not critical).
        critical: If True, forces disposition immediately regardless of score.
    """

    id: str
    description: str
    condition: Callable[[RuleContext], bool]
    forced_disposition: str
    escalation_script: str
    weight: int = 0
    critical: bool = False


# ---------------------------------------------------------------------------
# Rule result
# ---------------------------------------------------------------------------
@dataclass
class RedFlagRuleResult:
    """Aggregated result from running all red-flag rules."""

    forced_disposition: Optional[str] = None  # None = no override
    total_score: int = 0
    triggered_rule_ids: List[str] = field(default_factory=list)
    triggered_descriptions: List[str] = field(default_factory=list)
    escalation_script: Optional[str] = None
    all_triggers: List[Dict] = field(default_factory=list)  # Full audit detail


# ---------------------------------------------------------------------------
# Compiled pattern helper
# ---------------------------------------------------------------------------
def _matches(text: str, *patterns: str) -> bool:
    """Return True if any regex pattern matches text (case-insensitive)."""
    for p in patterns:
        if re.search(p, text, re.IGNORECASE | re.DOTALL):
            return True
    return False


# ---------------------------------------------------------------------------
# THE RED FLAG RULE REGISTRY — 10 mandatory rules
# ---------------------------------------------------------------------------
RED_FLAG_RULES: List[RedFlagRule] = [
    # ── CRITICAL RULES (instant ER_NOW) ────────────────────────────────
    RedFlagRule(
        id="CHEST_PAIN_SEVERE",
        description="Crushing/radiating chest pain — cardiac emergency",
        condition=lambda ctx: _matches(
            ctx.combined_text,
            r"chest\s+pain.*(crush|press|squeez|elephant|radiati)",
            r"(crush|press|squeez|elephant).*(chest|heart)",
            r"heart\s+attack",
            r"chest\s+pain.*(arm|jaw|neck|back|shoulder)",
            r"chest\s+pain.*(sweat|nausea|dizz|faint|short.?of.?breath)",
            r"(severe|worst|10.?out.?of|unbearable).*(chest\s+pain)",
        ),
        forced_disposition="ER_NOW",
        escalation_script=(
            "What you're describing sounds like it could be a cardiac emergency. "
            "Please call 9 1 1 immediately. Do not drive yourself. "
            "If you have aspirin available and are not allergic, chew one while you wait."
        ),
        weight=0,
        critical=True,
    ),
    RedFlagRule(
        id="BREATHING_FAILURE",
        description="Severe acute breathing difficulty / airway compromise",
        condition=lambda ctx: _matches(
            ctx.combined_text,
            r"can\s*'?\s*t\s+breath",
            r"cannot\s+breath",
            r"struggling\s+to\s+breath",
            r"gasping",
            r"choking",
            r"can\s*'?\s*t\s+get\s+(enough\s+)?air",
            r"lips?\s+(turning\s+)?blue",
            r"turning\s+blue",
        ),
        forced_disposition="ER_NOW",
        escalation_script=(
            "I'm hearing serious difficulty breathing. This could be a medical emergency. "
            "Please call 9 1 1 or have someone take you to the nearest ER right now."
        ),
        weight=0,
        critical=True,
    ),
    RedFlagRule(
        id="STROKE_SIGNS",
        description="Classic FAST stroke symptoms",
        condition=lambda ctx: _matches(
            ctx.combined_text,
            r"face\s+(is\s+)?(droop|numb|tingl)",
            r"(arm|leg)\s+(is\s+)?(weak|numb|can\s*'?\s*t\s+move)",
            r"slurr(ing|ed)\s+(speech|words)",
            r"sudden.*(confus|vision\s+loss|headache.*worst)",
            r"(worst|thunderclap)\s+headache",
            r"stroke",
            r"one\s+side.*(weak|numb|drooping)",
        ),
        forced_disposition="ER_NOW",
        escalation_script=(
            "You may be experiencing stroke symptoms. This is a time-critical emergency. "
            "Please call 9 1 1 immediately. Note the time your symptoms started."
        ),
        weight=0,
        critical=True,
    ),
    RedFlagRule(
        id="ANAPHYLAXIS",
        description="Severe allergic reaction / anaphylaxis",
        condition=lambda ctx: _matches(
            ctx.combined_text,
            r"(throat|tongue|lip).*(swell|clos|tight)",
            r"anaphyla",
            r"(can\s*'?\s*t|cannot)\s+(swallow|breath).*(swell|allerg)",
            r"epi\s*pen",
            r"severe\s+allerg",
        ),
        forced_disposition="ER_NOW",
        escalation_script=(
            "This sounds like it could be a severe allergic reaction. "
            "If you have an EpiPen, use it now. Call 9 1 1 immediately."
        ),
        weight=0,
        critical=True,
    ),
    RedFlagRule(
        id="UNCONTROLLED_BLEEDING",
        description="Uncontrolled or arterial haemorrhage",
        condition=lambda ctx: _matches(
            ctx.combined_text,
            r"(won\s*['']?\s*t|will\s+not|can\s*['']?\s*t|cannot)\s+stop\s+bleed",
            r"bleed.*(won\s*['']?\s*t|will\s+not|not)\s+stop",
            r"(heavy|severe|massive|uncontroll)\s+bleed",
            r"blood.*(everywhere|pouring|gushing|spurting)",
            r"arterial\s+bleed",
        ),
        forced_disposition="ER_NOW",
        escalation_script=(
            "It sounds like you have significant bleeding that is not stopping. "
            "Apply firm, direct pressure with a clean cloth. Call 9 1 1 immediately."
        ),
        weight=0,
        critical=True,
    ),
    RedFlagRule(
        id="LOSS_OF_CONSCIOUSNESS",
        description="Loss of consciousness or active seizure",
        condition=lambda ctx: _matches(
            ctx.combined_text,
            r"(passed|faint|black)\s*out",
            r"(lost|losing)\s+consciousness",
            r"unresponsive",
            r"(won\s*'?\s*t|will\s+not)\s+(wake|respond)",
            r"collaps(ed|ing)",
            r"seizur",
        ),
        forced_disposition="ER_NOW",
        escalation_script=(
            "Loss of consciousness or seizure activity is a medical emergency. "
            "Please call 9 1 1 right away."
        ),
        weight=0,
        critical=True,
    ),
    RedFlagRule(
        id="SUICIDAL_SELF_HARM",
        description="Suicidal ideation or active self-harm",
        condition=lambda ctx: _matches(
            ctx.combined_text,
            r"(want|going|plan)\s+(to\s+)?(kill|hurt|harm|end)\s+(my\s*self|my\s+life|it\s+all)",
            r"suicid",
            r"self[\s-]?harm",
            r"don\s*'?\s*t\s+want\s+to\s+(live|be\s+alive|go\s+on|exist)",
            r"(better|easier)\s+(off\s+)?dead",
            r"overdos",
        ),
        forced_disposition="ER_NOW",
        escalation_script=(
            "I hear that you may be going through a very difficult time. "
            "Your safety is the most important thing right now. "
            "Please call or text 9 8 8, the Suicide and Crisis Lifeline. "
            "You can also call 9 1 1. You are not alone."
        ),
        weight=0,
        critical=True,
    ),
    # ── WEIGHTED RULES (contribute to score; ≥10 = URGENT) ────────────
    RedFlagRule(
        id="HIGH_FEVER",
        description="High fever ≥103°F / ≥39.4°C",
        condition=lambda ctx: _matches(
            ctx.combined_text,
            r"fever.*(103|104|105|106|39\.[4-9]|4[0-2])",
            r"(103|104|105|106|39\.[4-9]|4[0-2]).*degree",
            r"very\s+high\s+fever",
        ),
        forced_disposition="URGENT",
        escalation_script=(
            "A fever that high needs prompt medical evaluation. "
            "Please contact urgent care or your doctor today."
        ),
        weight=5,
        critical=False,
    ),
    RedFlagRule(
        id="SEVERE_PAIN",
        description="Severe pain (8-10/10)",
        condition=lambda ctx: (
            _matches(
                ctx.combined_text,
                r"(8|9|10)\s*(out\s+of\s+10|\/\s*10|\s*on\s+scale)",
                r"pain.*(8|9|10).*/\s*10",
                r"(worst|unbearable|excruciating|10[\s/]10)\s+pain",
            )
            or ctx.symptom_severity == "severe"
        ),
        forced_disposition="URGENT",
        escalation_script=(
            "Severe pain at that level needs prompt medical attention. "
            "Please seek care today."
        ),
        weight=5,
        critical=False,
    ),
    RedFlagRule(
        id="PEDIATRIC_HIGH_FEVER",
        description="High fever in infant/toddler (<3 months with any fever)",
        condition=lambda ctx: (
            _matches(
                ctx.combined_text,
                r"(infant|baby|newborn|3\s*month|2\s*month|1\s*month).*(fever|temp)",
                r"fever.*(infant|baby|newborn|3\s*month)",
            )
            or (
                ctx.caller_age is not None
                and ctx.caller_age < 1
                and _matches(ctx.combined_text, r"fever|temp")
            )
        ),
        forced_disposition="URGENT",
        escalation_script=(
            "A fever in a very young child needs immediate medical evaluation. "
            "Please call your paediatrician or go to the ER now."
        ),
        weight=8,
        critical=False,
    ),
]

# Build lookup index
_RULE_INDEX: Dict[str, RedFlagRule] = {r.id: r for r in RED_FLAG_RULES}


def get_rule(rule_id: str) -> Optional[RedFlagRule]:
    """Look up a rule by ID."""
    return _RULE_INDEX.get(rule_id)


# ---------------------------------------------------------------------------
# Rule engine execution
# ---------------------------------------------------------------------------


def run_red_flag_rules(
    utterance: str = "",
    chief_complaint: Optional[str] = None,
    red_flags_reported: Optional[List[str]] = None,
    symptom_severity: Optional[str] = None,
    confusion_score: float = 0.0,
    caller_age: Optional[int] = None,
    pregnancy_status: Optional[str] = None,
    score_threshold: int = 10,
) -> RedFlagRuleResult:
    """Execute ALL red-flag rules against the given context.

    Execution order:
      1. Evaluate every rule (no short-circuit — all rules are evaluated for audit)
      2. If any CRITICAL rule triggered → forced_disposition = ER_NOW
      3. If total weighted score ≥ threshold → forced_disposition = URGENT
      4. Otherwise → forced_disposition = None (no override)

    Args:
        utterance: Raw caller utterance.
        chief_complaint: Accumulated chief complaint.
        red_flags_reported: LLM-extracted red flag strings.
        symptom_severity: Current severity level.
        confusion_score: 0–1 confusion score.
        caller_age: Caller's age if known.
        pregnancy_status: Pregnancy status if known.
        score_threshold: Weighted score threshold for URGENT (default 10).

    Returns:
        RedFlagRuleResult with all triggered rules and forced disposition.
    """
    ctx = RuleContext(
        utterance=utterance or "",
        chief_complaint=chief_complaint or "",
        red_flags_reported=red_flags_reported or [],
        symptom_severity=symptom_severity or "",
        confusion_score=confusion_score,
        caller_age=caller_age,
        pregnancy_status=pregnancy_status,
    )

    result = RedFlagRuleResult()
    any_critical = False
    first_critical_script = None

    for rule in RED_FLAG_RULES:
        try:
            triggered = rule.condition(ctx)
        except Exception as exc:
            # Rule evaluation exception → treat as triggered (conservative)
            logger.error(
                f"[RED_FLAG_RULES] Rule {rule.id} raised exception: {exc}. "
                "Treating as triggered (fail-closed)."
            )
            triggered = True

        if triggered:
            result.triggered_rule_ids.append(rule.id)
            result.triggered_descriptions.append(rule.description)
            result.all_triggers.append(
                {
                    "rule_id": rule.id,
                    "description": rule.description,
                    "forced_disposition": rule.forced_disposition,
                    "weight": rule.weight,
                    "critical": rule.critical,
                }
            )

            if rule.critical:
                any_critical = True
                if first_critical_script is None:
                    first_critical_script = rule.escalation_script
            else:
                result.total_score += rule.weight

            logger.info(
                f"[RED_FLAG_RULES] Triggered: {rule.id} "
                f"(critical={rule.critical}, weight={rule.weight})"
            )

    # Determine forced disposition
    if any_critical:
        result.forced_disposition = "ER_NOW"
        result.escalation_script = first_critical_script
    elif result.total_score >= score_threshold:
        result.forced_disposition = "URGENT"
        # Use the script from the highest-weight triggered rule
        weighted_triggers = [
            r
            for r in RED_FLAG_RULES
            if r.id in result.triggered_rule_ids and not r.critical
        ]
        if weighted_triggers:
            weighted_triggers.sort(key=lambda r: r.weight, reverse=True)
            result.escalation_script = weighted_triggers[0].escalation_script

    return result
