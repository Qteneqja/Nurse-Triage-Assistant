"""Webhook stability helpers for the Twilio voice channel (PR 1).

Pure state helpers over ``session.channel_metadata`` covering:

- duplicate webhook detection and idempotent replay (/incoming and /gather)
- consecutive-silence tracking with a fail-safe cap
- multi-segment narrative capture (long collision stories, Invariant: the
  caller is never cut off mid-story)
- vertical-aware fail-closed messaging (Birchwood: apologize + promise a
  callback + flag the record; healthcare wording is unchanged)
- one-time injury safety advisory for non-clinical verticals (Invariant 3)

All state lives under ``channel_metadata["webhook_stability"]`` so it is
persisted with the session and survives process restarts. Nothing here makes
LLM calls or alters safety decisions — observation and replay only.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime

from pydantic import BaseModel

from src.orchestrator.schemas import OrchestratorSession
from src.platform.workflows.schemas import ScriptedStageDefinition
from src.safety.injury_detection import INJURY_SAFETY_ADVISORY, scan_for_injuries

logger = logging.getLogger(__name__)

_STATE_KEY = "webhook_stability"

# A repeated POST with identical input inside this window is treated as a
# Twilio redelivery, not a caller repeating themselves (the assistant speaks
# between caller turns, so legitimate repeats arrive much later).
DUPLICATE_WINDOW_SECONDS = 15

# Consecutive empty Gather results before the call is closed fail-safe.
MAX_CONSECUTIVE_SILENCE = 3

# Narrative capture hard caps — generous enough for a two-minute story.
NARRATIVE_MAX_SEGMENTS = 4
NARRATIVE_MAX_CHARS = 3000

NARRATIVE_FIRST_CONTINUATION = (
    "Got it - go on, I'm listening. When you're finished, just say, that's everything."
)
NARRATIVE_NEXT_CONTINUATION = "Go on, I'm listening."

_COMPLETION_CUES = re.compile(
    r"(?:\bthat'?s?\s+(?:everything|all|about it|it)\b"
    r"|\bnothing else\b"
    r"|\bno that'?s?\s+(?:everything|all|it)\b"
    r"|\bi'?m (?:done|finished)\b"
    r"|\bthat would be (?:all|everything)\b"
    r"|^\s*(?:no|nope|nothing)\s*[.!]?\s*$)",
    re.IGNORECASE,
)

_TRAILING_CUE = re.compile(
    r"[,.\s]*(?:and\s+)?(?:that'?s?\s+(?:everything|all|about it|it)"
    r"|nothing else|i'?m (?:done|finished))\s*[.!]?\s*$",
    re.IGNORECASE,
)


class NarrativeIngest(BaseModel):
    """Result of feeding one utterance into narrative capture."""

    completed: bool
    joined_text: str = ""
    continuation_prompt: str = ""


def _state(session: OrchestratorSession) -> dict:
    return session.channel_metadata.setdefault(_STATE_KEY, {})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _age_seconds(iso_timestamp: str | None) -> float | None:
    if not iso_timestamp:
        return None
    try:
        then = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return None
    return (datetime.now(UTC) - then).total_seconds()


# ---------------------------------------------------------------------------
# Duplicate webhook detection / idempotent replay
# ---------------------------------------------------------------------------


def gather_input_fingerprint(
    call_sid: str,
    speech_result: str | None,
    digits: str | None,
) -> str:
    raw = f"{call_sid}|{(speech_result or '').strip()}|{(digits or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def duplicate_gather_replay(
    session: OrchestratorSession,
    fingerprint: str,
    speech_result: str | None,
) -> str | None:
    """Return the previously sent TwiML when this POST is a redelivery.

    A request is a redelivery only when ALL hold: the SpeechResult is
    non-empty (consecutive silences are legitimate repeats and must each
    count toward the silence cap), the input fingerprint matches the last
    processed input, it arrived within the duplicate window, and the session
    state shows that input was already incorporated (the last caller turn
    equals this SpeechResult). Worst-case false positive replays the
    previous prompt — state is never mutated twice.
    """
    speech = (speech_result or "").strip()
    if not speech:
        return None
    last = _state(session).get("last_gather") or {}
    if last.get("fingerprint") != fingerprint or not last.get("twiml"):
        return None
    age = _age_seconds(last.get("at"))
    if age is None or age > DUPLICATE_WINDOW_SECONDS:
        return None
    last_caller_text = next(
        (turn.text for turn in reversed(session.conversation) if turn.role == "caller"),
        None,
    )
    if last_caller_text != speech:
        return None
    return str(last["twiml"])


def note_gather_input(session: OrchestratorSession, fingerprint: str) -> None:
    state = _state(session)
    state["last_gather"] = {
        "fingerprint": fingerprint,
        "at": _now_iso(),
        "twiml": None,
    }


def remember_gather_twiml(session: OrchestratorSession, twiml: str) -> None:
    last = _state(session).setdefault("last_gather", {})
    last["twiml"] = twiml
    last.setdefault("at", _now_iso())


def remember_incoming_twiml(session: OrchestratorSession, twiml: str) -> None:
    _state(session)["incoming_twiml"] = twiml


def replay_incoming_twiml(session: OrchestratorSession) -> str | None:
    twiml = _state(session).get("incoming_twiml")
    return str(twiml) if twiml else None


# ---------------------------------------------------------------------------
# Consecutive-silence tracking
# ---------------------------------------------------------------------------


def record_silence(session: OrchestratorSession) -> int:
    state = _state(session)
    state["consecutive_silence"] = int(state.get("consecutive_silence", 0)) + 1
    return state["consecutive_silence"]


def reset_silence(session: OrchestratorSession) -> None:
    state = _state(session)
    if state.get("consecutive_silence"):
        state["consecutive_silence"] = 0


# ---------------------------------------------------------------------------
# Multi-segment narrative capture
# ---------------------------------------------------------------------------


def stage_is_multi_segment(stage: ScriptedStageDefinition | None) -> bool:
    return bool(stage is not None and getattr(stage, "multi_segment", False))


def narrative_segments(
    session: OrchestratorSession,
    stage: ScriptedStageDefinition,
) -> list[str]:
    narrative = _state(session).get("narrative") or {}
    if narrative.get("stage_id") != stage.stage_id:
        return []
    return list(narrative.get("segments") or [])


def narrative_has_segments(
    session: OrchestratorSession,
    stage: ScriptedStageDefinition | None,
) -> bool:
    if stage is None:
        return False
    return bool(narrative_segments(session, stage))


def narrative_ingest(
    session: OrchestratorSession,
    stage: ScriptedStageDefinition,
    speech_text: str,
) -> NarrativeIngest:
    """Feed one utterance into narrative capture for *stage*.

    Returns either a continuation (keep listening) or completion carrying
    the full joined narrative. Segments accumulate in session metadata so
    nothing is lost across webhook boundaries.
    """
    state = _state(session)
    narrative = state.get("narrative")
    if not narrative or narrative.get("stage_id") != stage.stage_id:
        narrative = {
            "stage_id": stage.stage_id,
            "segments": [],
            "started_at": _now_iso(),
        }
        state["narrative"] = narrative
    segments: list[str] = narrative["segments"]

    text = (speech_text or "").strip()
    cue_only = bool(text) and bool(_COMPLETION_CUES.fullmatch(text))
    if not cue_only:
        cleaned = _TRAILING_CUE.sub("", text).strip()
        has_trailing_cue = cleaned != text
        if cleaned:
            segments.append(cleaned)
        if has_trailing_cue:
            return _complete_narrative(state, segments)

    if cue_only:
        return _complete_narrative(state, segments)
    if len(segments) >= NARRATIVE_MAX_SEGMENTS:
        logger.info(
            "[TWILIO] Narrative segment cap reached (%s segments) — completing",
            len(segments),
        )
        return _complete_narrative(state, segments)
    if sum(len(s) for s in segments) >= NARRATIVE_MAX_CHARS:
        logger.info("[TWILIO] Narrative length cap reached — completing")
        return _complete_narrative(state, segments)

    prompt = (
        NARRATIVE_FIRST_CONTINUATION
        if len(segments) == 1
        else NARRATIVE_NEXT_CONTINUATION
    )
    return NarrativeIngest(completed=False, continuation_prompt=prompt)


def complete_narrative_on_silence(
    session: OrchestratorSession,
    stage: ScriptedStageDefinition,
) -> str | None:
    """Silence after part of a story means the caller is done, not absent."""
    segments = narrative_segments(session, stage)
    if not segments:
        return None
    return _complete_narrative(_state(session), segments).joined_text


def _complete_narrative(state: dict, segments: list[str]) -> NarrativeIngest:
    joined = " ".join(s for s in segments if s).strip()
    state["narrative"] = None
    return NarrativeIngest(completed=True, joined_text=joined)


# ---------------------------------------------------------------------------
# Injury safety advisory (Invariant 3) — non-clinical verticals only
# ---------------------------------------------------------------------------


def injury_advisory_if_needed(
    session: OrchestratorSession,
    speech_text: str | None,
) -> str | None:
    """Return the one-time injury advisory when *speech_text* warrants it.

    Healthcare sessions never get this layer — their safety stack already
    handles clinical content. The advisory is spoken at most once per call;
    the record-level flag is applied independently by the rules engine.
    """
    if _is_healthcare_workflow(session):
        return None
    state = _state(session)
    if state.get("injury_advisory_given"):
        return None
    scan = scan_for_injuries(speech_text)
    if not scan.mentioned:
        return None
    state["injury_advisory_given"] = True
    state["injury_matched_terms"] = scan.matched_terms
    scripted = session.channel_metadata.setdefault("scripted_intake", {})
    fields = scripted.setdefault("fields", {})
    fields["injury_mentions"] = scan.matched_terms
    logger.warning(
        "[TWILIO] Injury mention detected on call %s — advisory issued, record flagged",
        session.call_sid,
    )
    return INJURY_SAFETY_ADVISORY


def injury_advisory_for_recorded_state(
    session: OrchestratorSession,
) -> str | None:
    """Advisory keyed off the recorded injuries_state field (PR 2).

    A plain "yes" to the direct injury question carries no injury keywords,
    so the text scan never fires — the recorded, workflow-normalized state
    is the trigger instead. Same once-per-call guarantee.
    """
    if _is_healthcare_workflow(session):
        return None
    state = _state(session)
    if state.get("injury_advisory_given"):
        return None
    fields = (session.channel_metadata.get("scripted_intake") or {}).get("fields") or {}
    if fields.get("injuries_state") != "reported":
        return None
    state["injury_advisory_given"] = True
    logger.warning(
        "[TWILIO] Injury reported via direct question on call %s — advisory "
        "issued, record flagged",
        session.call_sid,
    )
    return INJURY_SAFETY_ADVISORY


def injury_advisory_already_given(session: OrchestratorSession) -> bool:
    return bool(_state(session).get("injury_advisory_given"))


def mark_injury_advisory_given(session: OrchestratorSession) -> None:
    _state(session)["injury_advisory_given"] = True


def join_preamble(*parts: str | None) -> str | None:
    joined = " ".join(p.strip() for p in parts if p and p.strip())
    return joined or None


# ---------------------------------------------------------------------------
# Vertical-aware fail-closed messaging
# ---------------------------------------------------------------------------

# Healthcare wording is intentionally identical to the pre-PR1 messages
# (Invariant 1: healthcare caller experience unchanged).
HEALTHCARE_ERROR_MESSAGE = "Sorry, we encountered an error. Please try again later."

NON_CLINICAL_ERROR_MESSAGE = (
    "I'm so sorry - we've hit a technical problem on our end. I've saved "
    "what you've told me so far, and someone from the team will call you "
    "back shortly. You don't need to do anything else right now."
)

HEALTHCARE_SILENCE_CLOSE = (
    "We haven't been able to hear you. If this is an emergency, please "
    "hang up and call 9 1 1. Otherwise, please call back when you're able. "
    "Goodbye."
)

NON_CLINICAL_SILENCE_CLOSE = (
    "I haven't been able to hear you, so I'll wrap up for now. I've saved "
    "what we have so far, and the team will call you back to finish up. "
    "Thanks for calling."
)


def _is_healthcare_workflow(session: OrchestratorSession | None) -> bool:
    if session is None:
        return True
    try:
        from src.verticals.healthcare.constants import HEALTHCARE_TRIAGE_WORKFLOW_ID

        healthcare_id = HEALTHCARE_TRIAGE_WORKFLOW_ID
    except Exception:
        healthcare_id = "healthcare_triage_v1"
    return (session.workflow_id or healthcare_id) == healthcare_id


def fail_closed_error_message(session: OrchestratorSession | None) -> str:
    if _is_healthcare_workflow(session):
        return HEALTHCARE_ERROR_MESSAGE
    return NON_CLINICAL_ERROR_MESSAGE


def silence_close_message(session: OrchestratorSession | None) -> str:
    if _is_healthcare_workflow(session):
        return HEALTHCARE_SILENCE_CLOSE
    return NON_CLINICAL_SILENCE_CLOSE


def flag_incomplete(session: OrchestratorSession, reason: str) -> None:
    """Mark the session's record as incomplete/flagged — additive only."""
    state = _state(session)
    flags = state.setdefault("flags", [])
    for flag in ("incomplete", reason):
        if flag not in flags:
            flags.append(flag)
    scripted = session.channel_metadata.setdefault("scripted_intake", {})
    fields = scripted.setdefault("fields", {})
    fields.setdefault("incomplete_reason", reason)
