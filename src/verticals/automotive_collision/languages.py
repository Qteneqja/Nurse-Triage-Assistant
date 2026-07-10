"""Language selection + (prompt, language) catalog for the Birchwood vertical.

PLUMBING ONLY — this module makes language a first-class dimension of the
Birchwood spoken copy without shipping French. It owns:

- the supported-language constants and the per-session language state
  (stored in ``session.channel_metadata`` — a presentation/routing concern;
  the orchestrator and safety layers never read it);
- the bilingual language-menu prompt (gated by
  ``config.BIRCHWOOD_FRENCH_ENABLED``, default OFF — when the flag is off
  the menu never plays and every call proceeds in English exactly as
  before);
- the ``(prompt_key, language)`` catalog: English is fully populated from
  the canonical copy in ``prompts.py``/the workflow's final messages; the
  French side is deliberately ``[FR TODO]`` placeholders pending HUMAN /
  professional translation. Do NOT machine-translate this catalog — the
  safety-critical lines (:data:`SAFETY_CRITICAL_PROMPT_KEYS`) especially.

FAIL-CLOSED RULE: :func:`get_prompt` never returns an untranslated
placeholder. A missing or ``[FR TODO]``-marked French entry resolves to the
English canonical text, so a caller can never hear placeholder copy — the
call simply stays English until real translation lands.

FRENCH LAUNCH BLOCKERS (before BIRCHWOOD_FRENCH_ENABLED may be turned on):
- professional translation of every catalog entry below (safety-critical
  keys reviewed, never machine-translated);
- French coverage for the spoken surfaces that live OUTSIDE this catalog
  and intentionally stay English fail-closed today: the platform injury
  advisory, Aurora naturalness variants/backchannels
  (``voice_naturalness.py``), silence/validation reprompts, and the
  shared stability closes in ``src/twilio/webhook_stability.py``;
- verified French synthesis on the configured TTS voice: the current
  ``en-US-Bree:DragonHDLatestNeural`` voice has NO documented French
  support, and ``azure_tts._build_ssml`` hardcodes ``xml:lang='en-US'`` —
  a per-language voice (e.g. a documented fr-CA/fr-FR voice) plus locale
  plumbing in the TTS layer is required;
- ConversationRelay wiring: the menu is wired on the gather transport;
  this module is transport-neutral so CR reuses the same catalog and
  session-language helpers when its turn comes.
"""

from __future__ import annotations

import src.config as config
from src.verticals.automotive_collision.prompts import (
    BIRCHWOOD_COLLISION_CONVERSATIONAL_INTRO,
    BIRCHWOOD_COLLISION_CONVERSATIONAL_OUTRO,
    BIRCHWOOD_COLLISION_INTRO,
    BIRCHWOOD_COLLISION_NEXT_STEPS_CLOSE,
    BIRCHWOOD_COLLISION_PROMPTS,
    BIRCHWOOD_TRANSFER_CONNECTING_MESSAGE,
    BIRCHWOOD_TRANSFER_DIAL_FALLBACK_MESSAGE,
)

LANGUAGE_ENGLISH = "en"
LANGUAGE_FRENCH = "fr"
DEFAULT_LANGUAGE = LANGUAGE_ENGLISH
SUPPORTED_LANGUAGES = (LANGUAGE_ENGLISH, LANGUAGE_FRENCH)

# Marker for an untranslated catalog entry. get_prompt() treats any entry
# carrying this marker as ABSENT (fail closed to English).
FR_TODO_MARKER = "[FR TODO]"

# Spoken before any language is chosen, so it is bilingual by design. Static,
# pre-approved copy — never composed by the LLM. Played only when
# BIRCHWOOD_FRENCH_ENABLED is true.
BIRCHWOOD_LANGUAGE_MENU_PROMPT = (
    "For English, press 1. Pour le français, appuyez sur le 2."
)

# Keys whose copy is safety-relevant (emergency/injury reflex, the
# automated-assistant + recording disclosure, transfer/callback promises).
# These MUST be professionally translated and reviewed before the French
# side goes live; the fail-closed lookup keeps them English until then.
SAFETY_CRITICAL_PROMPT_KEYS = (
    "intro",
    "intro_conversational",
    "injuries_state",
    "transfer_connecting",
    "transfer_dial_fallback",
    "next_steps_close",
    "final_transfer_collision_center",
    "final_transfer_glass_department",
    "final_incomplete_callback",
    "final_human_review",
)

# ---------------------------------------------------------------------------
# The catalog. English is the canonical copy (single source: prompts.py for
# the scripted/transfer lines; the final_* keys are the deterministic spoken
# final messages, moved here verbatim from workflow._spoken_final_message so
# they are looked up by (key, language) instead of hardcoded English).
# ---------------------------------------------------------------------------

_ENGLISH_PROMPTS: dict[str, str] = {
    "intro": BIRCHWOOD_COLLISION_INTRO,
    "intro_conversational": BIRCHWOOD_COLLISION_CONVERSATIONAL_INTRO,
    "conversational_outro": BIRCHWOOD_COLLISION_CONVERSATIONAL_OUTRO,
    "transfer_connecting": BIRCHWOOD_TRANSFER_CONNECTING_MESSAGE,
    "transfer_dial_fallback": BIRCHWOOD_TRANSFER_DIAL_FALLBACK_MESSAGE,
    "next_steps_close": BIRCHWOOD_COLLISION_NEXT_STEPS_CLOSE,
    # Scripted stage prompts, keyed by field_name (the stage's store key).
    **BIRCHWOOD_COLLISION_PROMPTS,
    # Deterministic final messages (workflow._spoken_final_message).
    "final_transfer_collision_center": (
        "No problem at all. Since the vehicle may not be safe to drive - "
        "or you'd just rather talk with a person - let me get you "
        "straight through to our collision team."
    ),
    "final_transfer_glass_department": (
        "That sounds like glass-only damage, and our glass team takes "
        "care of those directly - let me get this over to them for you."
    ),
    "final_declined_vehicle_year": (
        "I really appreciate you calling. Unfortunately, our collision "
        "centers are only able to take vehicles from 2012 and newer. "
        "Thanks so much for thinking of Birchwood."
    ),
    "final_declined_rebuilt_salvage": (
        "Thanks for letting me know. Unfortunately, our collision "
        "centers aren't able to service rebuilt or salvage title "
        "vehicles. I really appreciate you calling Birchwood."
    ),
    # Prefixes are concatenated with next_steps_close — keep the trailing
    # space so the assembled sentence stays byte-identical.
    "final_private_pay_prefix": (
        "No problem at all - I've noted the repair as out of pocket. "
    ),
    "final_missing_claim_number": (
        "That's no problem at all - I've noted that the claim number is "
        "still to come, and your advisor will grab it when they call "
        "you back. Thanks so much for calling Birchwood, and take care."
    ),
    "final_incomplete_callback": (
        "Thanks so much. I've saved everything you've told me, and one "
        "of our service advisors will call you back to fill in the last "
        "couple of details. Thanks for calling Birchwood, and take care."
    ),
    "final_human_review": (
        "Thank you. I've passed this along for our team to double-check "
        "a few details, and someone will follow up with you shortly. "
        "Thanks for calling Birchwood."
    ),
    "final_readback_correction_prefix": (
        "Thanks - I've noted that correction for your advisor to double-check. "
    ),
}

# French: the parallel structure, every entry a clearly-marked untranslated
# placeholder. When professional translation lands, replace this
# comprehension with an explicit dict of the translated strings (parity with
# the English keys is enforced by tests either way).
_FRENCH_PROMPTS: dict[str, str] = {
    key: f"{FR_TODO_MARKER} {text}" for key, text in _ENGLISH_PROMPTS.items()
}

PROMPT_CATALOG: dict[str, dict[str, str]] = {
    LANGUAGE_ENGLISH: _ENGLISH_PROMPTS,
    LANGUAGE_FRENCH: _FRENCH_PROMPTS,
}


# ---------------------------------------------------------------------------
# Config gates
# ---------------------------------------------------------------------------


def french_enabled() -> bool:
    """The BIRCHWOOD_FRENCH_ENABLED master gate (default OFF)."""
    return bool(getattr(config, "BIRCHWOOD_FRENCH_ENABLED", False))


def language_menu_enabled() -> bool:
    """Whether the call-start language menu should play at all."""
    return french_enabled()


# ---------------------------------------------------------------------------
# Catalog lookup — the fail-closed core
# ---------------------------------------------------------------------------


def is_untranslated_placeholder(text: object) -> bool:
    """True when ``text`` must never be spoken to a caller."""
    if not isinstance(text, str) or not text.strip():
        return True
    return FR_TODO_MARKER in text


def has_translation(prompt_key: str, language: str) -> bool:
    """True when a REAL (non-placeholder) entry exists for the pair."""
    if language == DEFAULT_LANGUAGE:
        return prompt_key in _ENGLISH_PROMPTS
    candidate = PROMPT_CATALOG.get(language, {}).get(prompt_key)
    return candidate is not None and not is_untranslated_placeholder(candidate)


def get_prompt(prompt_key: str, language: str = DEFAULT_LANGUAGE) -> str:
    """Spoken copy for ``(prompt_key, language)`` — NEVER a placeholder.

    English is canonical; an unknown key raises ``KeyError`` (a programming
    error, not a caller-facing state). For any other language the entry is
    used only when it exists and carries no ``[FR TODO]`` marker; otherwise
    the ENGLISH text is returned — fail closed, so an untranslated or
    partially translated catalog can never leak placeholder copy (or a
    half-French call) to a caller.
    """
    english = _ENGLISH_PROMPTS[prompt_key]
    if language == DEFAULT_LANGUAGE:
        return english
    candidate = PROMPT_CATALOG.get(language, {}).get(prompt_key)
    if candidate is None or is_untranslated_placeholder(candidate):
        return english
    return candidate


# ---------------------------------------------------------------------------
# Menu-choice parsing + per-session language state
# ---------------------------------------------------------------------------


def resolve_language_choice(
    speech_text: str | None,
    digits: str | None,
) -> str | None:
    """Map a language-menu answer to a supported language, else ``None``.

    DTMF is the advertised mechanism ("press 1 / appuyez sur le 2"); spoken
    language NAMES are accepted defensively. Number WORDS are deliberately
    not matched — phone-audio STT confuses "two"/"tow" and similar, and a
    wrong language pick is worse than one reprompt.
    """
    if digits == "1":
        return LANGUAGE_ENGLISH
    if digits == "2":
        return LANGUAGE_FRENCH
    normalized = (speech_text or "").strip().lower()
    if not normalized:
        return None
    if "english" in normalized or "anglais" in normalized:
        return LANGUAGE_ENGLISH
    if "french" in normalized or "français" in normalized or "francais" in normalized:
        return LANGUAGE_FRENCH
    return None


def get_session_language(session) -> str:
    """The session's selected language (validated; defaults to English)."""
    if session is None:
        return DEFAULT_LANGUAGE
    metadata = getattr(session, "channel_metadata", None) or {}
    language = metadata.get("language")
    if language in SUPPORTED_LANGUAGES:
        return language
    return DEFAULT_LANGUAGE


def set_session_language(session, language: str) -> None:
    """Record the selected language on the session (channel metadata only)."""
    if language not in SUPPORTED_LANGUAGES:
        language = DEFAULT_LANGUAGE
    session.channel_metadata["language"] = language
    selection = session.channel_metadata.setdefault("language_selection", {})
    selection["selected"] = language
