"""Deterministic voice-naturalness helpers for the Birchwood collision intake.

Aurora naturalness pass (Track A). Everything here is:

* **Deterministic** — no LLM is ever called; these are hand-authored phrasing
  pools, slot-echoes, backchannels, fillers, and an off-topic redirection
  helper. Because none of it is generated, none of it needs the LLM safety
  gate (scripted prompts have never been gated); it is plain scripted copy.
* **Transport-neutral** — consumed through the workflow's existing
  ``build_dynamic_prompt`` / acknowledgement / scripted-response seams, which
  BOTH the gather path and the ConversationRelay path already call. Nothing
  here imports a transport, so it carries into Track B unchanged.
* **Additive** — extraction fields, the rules engine (``rules.py``), the
  safety branches, the orchestrator, and the safety gate are untouched.

Selection avoids repeating the same variant twice in a row within a call by
remembering the last index per pool key in ``session.channel_metadata``. A
stage's rendered prompt is memoized by ``stage_id`` so the text logged to the
transcript and the text spoken in TwiML are identical — the gather path renders
a stage prompt twice per turn (once to log, once to synthesize).

The injury question is treated as safety-relevant: it gets only minimal,
meaning-preserving variation and is never softened.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from src.orchestrator.schemas import OrchestratorSession
from src.platform.workflows.schemas import ScriptedStageDefinition
from src.safety.injury_detection import scan_for_injuries
from src.verticals.automotive_collision.constants import (
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)

# ---------------------------------------------------------------------------
# Phrasing pools — 3-5 equivalent variants per scripted stage. Each pool's
# variants are meaning-equivalent; the selector picks one per call and avoids
# an immediate repeat. The canonical line in ``prompts.py`` remains the static
# fallback used whenever a pool is absent or selection fails.
# ---------------------------------------------------------------------------

INTAKE_PHRASING_POOLS: dict[str, list[str]] = {
    "caller_name": [
        "First, who am I speaking with today — your full name?",
        "Let's start with your name — what's your full name?",
        "Can I grab your full name to get started?",
        "To begin, may I have your full name, please?",
    ],
    "phone": [
        "And what's the best phone number to reach you at?",
        "What's the best number for us to call you back on?",
        "Could I get the best phone number to reach you?",
        "And a good callback number for you?",
    ],
    "vehicle_year": [
        "What year is the vehicle?",
        "What's the year of the vehicle?",
        "And the year of the car?",
        "Let's start with the vehicle — what year is it?",
    ],
    "vehicle_make": [
        "And the make of the vehicle?",
        "What's the make — Toyota, Honda, that kind of thing?",
        "Who makes it?",
        "And the make — a Ford, a Honda, a Toyota?",
    ],
    "vehicle_model": [
        "And the model?",
        "What model is it?",
        "And which model?",
        "What's the model?",
    ],
    "damage_type": [
        "In a sentence or two, what's the damage to the vehicle?",
        "Can you describe the damage for me — just a sentence or two?",
        "What's the damage like? A quick description is perfect.",
        "Tell me about the damage — a sentence or two is plenty.",
    ],
    "is_drivable": [
        "Can you drive the vehicle in to us right now, or does it need a tow?",
        "Is it safe to drive in, or will it need a tow?",
        "Can it be driven in, or should we arrange a tow?",
        "Is the car drivable, or does it need towing?",
    ],
    "filing_insurance_claim": [
        "Have you opened an MPI claim for this yet? A yes or no is perfect.",
        "Have you started an MPI claim yet? Either way is fine.",
        "Are you going through MPI on this one, or paying privately?",
        "Have you filed an MPI claim yet? No worries if you haven't.",
    ],
    "claim_number": [
        "If you've got the MPI claim number handy, what is it?",
        "Do you have the claim number? Totally fine if you don't yet.",
        "What's the claim number, if you have it?",
        "If you have your MPI claim number, go ahead — otherwise no problem.",
    ],
    "correction_note": [
        "No problem at all — what should I fix?",
        "Of course — what would you like me to correct?",
        "Sure thing — what should I change?",
    ],
    # Richer-flow stages (not in the live minimal stage list, but kept so the
    # pass also covers birchwood_collision_intake_v1's fuller variant).
    "incident_description": [
        "Whenever you're ready, walk me through what happened, in your own "
        "words. I'll listen first, then ask a few quick follow-ups.",
        "Take your time and tell me what happened, from the beginning. I'll "
        "just listen, then follow up with a couple of questions.",
        "Go ahead and tell me the story of what happened, in your own words "
        "— I'll listen all the way through first.",
    ],
    "rebuilt_salvage_status": [
        "Has the vehicle ever been written off and rebuilt? It would show as "
        "rebuilt or salvage on the title.",
        "Quick title question — has it ever been a rebuilt or salvage title?",
        "Has it ever been rebuilt after a write-off, or carried a salvage title?",
    ],
    "incident_datetime": [
        "When did this happen? Roughly is fine.",
        "And when did it happen — a rough idea is plenty.",
        "Do you remember when it happened? Approximately is fine.",
    ],
    "incident_location": [
        "And where did it happen? A street, intersection, or lot is perfect.",
        "Where did this take place — a street or intersection is plenty.",
        "And whereabouts did it happen?",
    ],
}

# Injury question — SAFETY-RELEVANT. Minimal, meaning-preserving variation only;
# every variant leads with the injury check and is never softened. The minimal
# live flow does not ask this (the spontaneous-injury reflex is the shared
# platform overlay), but the richer variant does, so it is covered here too.
INJURIES_STATE_VARIANTS: list[str] = [
    "Before anything else — was anyone hurt, even a little?",
    "First and most important — was anyone injured at all?",
]

# Closing message variants. EVERY variant preserves the no-promise disclaimer
# verbatim (this doesn't confirm coverage/pricing/appointment) — that clause is
# a forbidden-promise guard, not decorative, so it is asserted in tests.
_CLOSE_DISCLAIMER = (
    "Just so you know, this doesn't confirm coverage, pricing, or an "
    "appointment yet - your advisor will take care of those details with you."
)
# Every variant keeps two scaffolds: "Here's what happens next:" (warm, sets
# expectations) and the no-promise disclaimer (a forbidden-promise guard). Both
# are asserted in tests; only the surrounding phrasing varies.
CLOSE_VARIANTS: list[str] = [
    "Perfect - you're all set, and we'll get you booked in. Here's what "
    "happens next: one of our service advisors will give you a call back to "
    "confirm timing and the Birchwood location that works best for you. "
    + _CLOSE_DISCLAIMER
    + " Thanks so much for calling Birchwood, and take care.",
    "Wonderful - that's everything I need. Here's what happens next: a "
    "Birchwood service advisor will call you back to confirm timing and the "
    "best location for you. "
    + _CLOSE_DISCLAIMER
    + " Thanks so much for calling Birchwood, and take care.",
    "Great - you're booked in. Here's what happens next: one of our service "
    "advisors will call you back to sort out timing and the location that "
    "works best for you. "
    + _CLOSE_DISCLAIMER
    + " Thank you for calling Birchwood, and take care.",
]

# ---------------------------------------------------------------------------
# Backchannels — short acknowledgements of the PREVIOUS answer, keyed to the
# answered stage's speech profile. Brisk after a short slot, softer after the
# longer incident narrative.
# ---------------------------------------------------------------------------

BACKCHANNEL_POOLS: dict[str, list[str]] = {
    "short_field": ["Perfect.", "Got it.", "Okay.", "Great.", "Alright."],
    "narrative": ["Sure.", "Of course.", "No worries.", "Thank you.", "Got it."],
}

# Slot-echo connectors (ack-free — the backchannel supplies any "okay"/"got it",
# so the echo must not stack a second acknowledgement, and we avoid a leading
# "So," which reads as a tic when it fires every stage).
_ECHO_FORMS: list[str] = ["A {value}. ", "That's a {value}. ", "It's a {value}. "]

# A single light hesitation budget per CALL (a "filled pause"/self-correction).
# Applied to the first composed prompt only — clean and professional, never per
# turn. Bias is deliberately toward variety + slot-echo (which never sound
# fake); this is one subtle touch, not a tic.
DISFLUENCY_OPENERS: list[str] = ["Okay — ", "Alright — ", "Let's see — "]

# Verbal fillers for the /thinking poll loop (mask the STT-finalization gap and
# the readback build). Pre-rendered as audio by scripts/prerender_birchwood_fillers.py.
FILLER_POOL: list[str] = [
    "Okay, one sec.",
    "Let me just get that down.",
    "Bear with me a moment.",
    "Just a moment.",
    "Alright, one moment.",
]

# Off-topic redirection wording pools.
REDIRECT_ACK_POOL: list[str] = [
    "Totally,",
    "Sure,",
    "Good question,",
    "No problem,",
    "Of course,",
]
REDIRECT_BRIDGE_POOL: list[str] = [
    "and we can sort that out in a moment —",
    "and your advisor can go over that when they call —",
    "and we'll come back to that —",
    "the specialist can cover that for you —",
]

# Number of off-topic redirects allowed per stage before handing off (the
# unparsed required field is left missing, so the deterministic rules return
# INCOMPLETE_CALLBACK_NEEDED — a human callback, no rules/engine change).
REDIRECT_CAP = 2

# Off-topic signals require a QUESTION FRAME, never a bare keyword. A caller
# surnamed "Price"/"Quote", or one describing damage as "going to cost a lot" or
# "the estimate is bad", must stay ON-topic — only a genuine meta-question/tangent
# counts. (The earlier bare-word patterns dropped legitimate required fields.)
_META_QUESTION_PATTERNS: list[str] = [
    r"how much (?:will|does|is|to|for|would|are|do|can)",
    r"how much is (?:this|it|that)",
    r"how long (?:will|does|is|to|until|before|are|do|can)",
    r"what(?:'s| is| will| would| does).{0,25}(?:cost|price|charge|quote|estimate)",
    r"are you (?:a )?(?:real|robot|human|an? ai|recording|person|machine)",
    r"is this (?:a )?(?:recording|real person|robot|machine|live)",
    r"am i (?:talking|speaking) to (?:a )?(?:robot|machine|person|human|real)",
    r"what happens (?:next|after)",
    r"can i ask (?:you )?(?:a |something|one)",
    r"i have a (?:quick )?question",
    r"do you (?:do|offer|cover|handle|take|accept)\b",
]
# Word-boundary question words (so "what" no longer matches inside "somewhat").
_QUESTION_WORD_RE = re.compile(r"\b(?:how|what|why|when|where|which)\b")
_QUESTION_PHRASES: list[str] = [
    "do you",
    "can you",
    "will you",
    "could you",
    "is there",
    "are you",
]


@dataclass
class RedirectDecision:
    """Outcome of an off-topic check.

    ``reprompt`` — speak this (ack + bridge + re-ask) and stay on the stage.
    ``advance``  — the per-stage cap was exceeded; skip the stage WITHOUT
                   recording (the required field stays missing → callback).
    """

    reprompt: str | None = None
    advance: bool = False


# ---------------------------------------------------------------------------
# Session gate + selection state
# ---------------------------------------------------------------------------


def is_birchwood_session(session: OrchestratorSession | None) -> bool:
    return bool(
        session is not None and session.workflow_id == BIRCHWOOD_COLLISION_WORKFLOW_ID
    )


def _nv(session: OrchestratorSession) -> dict:
    return session.channel_metadata.setdefault("voice_naturalness", {})


def select_variant(session: OrchestratorSession, key: str, pool: list[str]) -> str:
    """Pick a pool variant for ``key``, never repeating the immediately prior one."""
    if not pool:
        return ""
    if len(pool) == 1:
        return pool[0]
    nv = _nv(session)
    last_indexes = nv.setdefault("last_index", {})
    last = last_indexes.get(key)
    candidates = [i for i in range(len(pool)) if i != last] or list(range(len(pool)))
    idx = random.choice(candidates)
    last_indexes[key] = idx
    return pool[idx]


def _phrasing_pool(field_name: str) -> list[str]:
    if field_name == "injuries_state":
        return INJURIES_STATE_VARIANTS
    return INTAKE_PHRASING_POOLS.get(field_name, [])


# ---------------------------------------------------------------------------
# Slot-echo — echo already-captured fields back as the flow proceeds. The
# strongest "I'm really listening" signal, fully deterministic.
# ---------------------------------------------------------------------------


def _clean_str(value: object) -> str:
    text = str(value).strip() if value is not None else ""
    return text


def _vehicle_phrase(fields: dict, *, parts: tuple[str, ...]) -> str:
    chunks = [_clean_str(fields.get(p)) for p in parts]
    return " ".join(c for c in chunks if c)


def slot_echo_preamble(
    session: OrchestratorSession,
    fields: dict,
    field_name: str,
) -> str:
    """Return a short echo of prior answers for ``field_name`` (or '')."""
    # Echo only where it sounds natural — after the make (year+make) and once the
    # full vehicle is known (at damage). A year-only echo before the make ("A
    # 2019. Who makes it?") read as a tic, so it's intentionally dropped.
    value = ""
    if field_name == "vehicle_model":
        value = _vehicle_phrase(fields, parts=("vehicle_year", "vehicle_make"))
    elif field_name == "damage_type":
        value = _vehicle_phrase(
            fields, parts=("vehicle_year", "vehicle_make", "vehicle_model")
        )
    if not value:
        return ""
    form = select_variant(session, f"echo:{field_name}", _ECHO_FORMS)
    return form.format(value=value)


# ---------------------------------------------------------------------------
# Dynamic prompt composition (consumed by build_dynamic_prompt → both transports)
# ---------------------------------------------------------------------------


def compose_stage_prompt(
    session: OrchestratorSession,
    stage: ScriptedStageDefinition,
    fields: dict,
) -> str | None:
    """Slot-echo + a per-call phrasing variant for ``stage`` (memoized per stage).

    Returns ``None`` when there is no phrasing pool for the field, so the caller
    falls back to the static prompt. Birchwood-gated.
    """
    if not is_birchwood_session(session):
        return None
    pool = _phrasing_pool(stage.field_name)
    if not pool:
        return None

    nv = _nv(session)
    cache = nv.setdefault("prompt_cache", {})
    cached = cache.get(stage.stage_id)
    if isinstance(cached, str):
        return cached

    variant = select_variant(session, f"prompt:{stage.field_name}", pool)
    echo = slot_echo_preamble(session, fields, stage.field_name)
    text = f"{echo}{variant}" if echo else variant
    # SAFETY: never prefix a hesitation/embellishment to the injury question —
    # it must be delivered verbatim, minimal, and never softened.
    if stage.field_name != "injuries_state":
        text = _maybe_lead_disfluency(session, text)
    cache[stage.stage_id] = text
    return text


def _maybe_lead_disfluency(session: OrchestratorSession, text: str) -> str:
    """Prefix one light hesitation, at most once per call.

    The openers end with an em-dash, so the original prompt's first word becomes
    mid-utterance — lower-case it (unless it's "I" or an all-caps token like
    "MPI") so the transcript reads naturally.
    """
    nv = _nv(session)
    if nv.get("disfluency_used"):
        return text
    nv["disfluency_used"] = True
    opener = select_variant(session, "disfluency", DISFLUENCY_OPENERS)
    first_word = text.split(maxsplit=1)[0] if text else ""
    if (
        first_word
        and first_word != "I"
        and first_word[0].isupper()
        and not first_word.isupper()
    ):
        text = text[0].lower() + text[1:]
    return f"{opener}{text}"


# ---------------------------------------------------------------------------
# Backchannel (consumed by _scripted_stage_acknowledgement → both transports)
# ---------------------------------------------------------------------------


def backchannel(
    session: OrchestratorSession,
    answered_stage: ScriptedStageDefinition,
) -> str | None:
    """A short, varied acknowledgement of the just-answered stage (or None)."""
    if not is_birchwood_session(session):
        return None
    profile = answered_stage.speech_profile or "short_field"
    pool = BACKCHANNEL_POOLS.get(profile, BACKCHANNEL_POOLS["short_field"])
    return select_variant(session, f"ack:{profile}", pool)


# ---------------------------------------------------------------------------
# Filler for the /thinking loop
# ---------------------------------------------------------------------------


def pick_filler(session: OrchestratorSession) -> str:
    return select_variant(session, "filler", FILLER_POOL)


def select_close(session: OrchestratorSession) -> str:
    """A varied closing line (or '' for non-Birchwood → caller uses the canonical)."""
    if not is_birchwood_session(session):
        return ""
    return select_variant(session, "close", CLOSE_VARIANTS)


# ---------------------------------------------------------------------------
# Off-topic redirection
# ---------------------------------------------------------------------------


def _looks_like_question(text: str) -> bool:
    lowered = text.lower().strip()
    if lowered.endswith("?"):
        return True
    if _QUESTION_WORD_RE.search(lowered):
        return True
    return any(phrase in lowered for phrase in _QUESTION_PHRASES)


def _matches_meta_question(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered) for p in _META_QUESTION_PATTERNS)


def _parses_as_stage_answer(stage: ScriptedStageDefinition, text: str) -> bool:
    """True when ``text`` plausibly answers ``stage`` (so it is NOT off-topic)."""
    from src.verticals.automotive_collision.rules import (
        parse_intake_bool,
        parse_vehicle_year,
    )

    field_type = (stage.field_type or stage.expected_answer_type or "free_text").lower()
    field = stage.field_name
    if field == "vehicle_year" or field_type in {"integer", "number"}:
        return parse_vehicle_year(text) is not None
    if field == "is_drivable":
        return parse_intake_bool(text, kind="drivable") is not None
    if field == "filing_insurance_claim":
        return parse_intake_bool(text, kind="insurance") is not None
    # Free-text / name / phone: treat any non-empty, non-meta utterance as a
    # plausible answer (conservative — avoid skipping a real answer).
    return True


def is_off_topic(stage: ScriptedStageDefinition, utterance: str) -> bool:
    """Conservative off-topic detector (no safety calls — caller gates those).

    A legitimate answer that merely CONTAINS a keyword (a surname like "Price",
    damage described as "going to cost a lot") stays on-topic — only a genuine
    meta-question frame counts. This bias protects required free-text fields:
    we would rather record an odd-but-real answer than drop it to a callback.
    """
    text = (utterance or "").strip()
    if not text:
        return False
    if _parses_as_stage_answer(stage, text):
        # Plausibly answers the stage. The only way a "parsing" answer is still
        # off-topic is a clear meta-question tangent (this is also the sole path
        # for a free-text stage, where _parses_as_stage_answer is always True).
        return _matches_meta_question(text)
    # Does not parse as the typed stage's expected value:
    if _matches_meta_question(text):
        return True
    return _looks_like_question(text)


def evaluate_redirect(
    session: OrchestratorSession,
    stage: ScriptedStageDefinition,
    utterance: str,
) -> RedirectDecision | None:
    """Decide whether to redirect an off-topic utterance back to ``stage``.

    Yields to safety/transfer: returns ``None`` for an injury mention or a
    transfer request so those higher-priority paths run unchanged. Returns
    ``None`` for non-Birchwood sessions and for on-topic answers.
    """
    if not is_birchwood_session(session):
        return None
    text = (utterance or "").strip()
    if not text:
        return None

    # Safety/transfer take precedence — never redirect over them.
    if scan_for_injuries(text).mentioned:
        return None
    if _is_transfer_phrase(text):
        return None

    if not is_off_topic(stage, text):
        return None

    nv = _nv(session)
    counts = nv.setdefault("redirects", {})
    count = int(counts.get(stage.stage_id, 0)) + 1
    counts[stage.stage_id] = count

    if count > REDIRECT_CAP:
        nv.setdefault("redirect_exhausted", [])
        if stage.stage_id not in nv["redirect_exhausted"]:
            nv["redirect_exhausted"].append(stage.stage_id)
        return RedirectDecision(reprompt=None, advance=True)

    ack = select_variant(session, "redirect_ack", REDIRECT_ACK_POOL)
    bridge = select_variant(session, "redirect_bridge", REDIRECT_BRIDGE_POOL)
    reask = stage.prompt_text or stage.prompt
    return RedirectDecision(reprompt=f"{ack} {bridge} {reask}", advance=False)


def _is_transfer_phrase(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized == "transfer" or normalized.startswith("transfer "):
        return True
    return any(
        phrase in normalized
        for phrase in [
            "speak with someone",
            "person please",
            "human please",
            "representative",
            "operator",
        ]
    )
