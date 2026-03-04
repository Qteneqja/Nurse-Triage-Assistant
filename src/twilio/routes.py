"""
Twilio Voice Routes — Phase 5 SaaS Infrastructure

All session state goes through SessionRepository → StorageFactory.
All LLM calls go through Orchestrator → GuardedLLM → SafetyGate.
Twilio signature validation enforced via router-level dependency.
"""

import asyncio
import logging
import json
from datetime import datetime
from pathlib import Path
from xml.sax import saxutils
from fastapi import APIRouter, Depends, Request, Form, BackgroundTasks
from fastapi.responses import Response
from typing import Optional

from src.storage.session_repository import get_session_repository
from src.orchestrator.orchestrator import get_orchestrator
from src.orchestrator.schemas import ConversationTurn
from src.safety.phi_masking import mask_phi
from src.config import STORE_PHI
from src.security.twilio_signature import validate_twilio_signature
from src.utils.report_naming import (
    generate_report_filename,
    generate_report_path,
    ensure_year_folders,
)
from src.utils.blob_storage import upload_reports_to_blob
from src.utils.azure_tts import text_to_speech_url

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(validate_twilio_signature)])

# Create reports directory and year/month folder structure
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)
ensure_year_folders(REPORTS_DIR)


async def generate_handoff_report_background(
    session_id: str, session_metadata: dict, conversation_history: list, triage_result
):
    """DEPRECATED — legacy handoff report path.

    This function previously called DeepSeekClient directly (ungated).
    All report generation now goes through _generate_orchestrator_report_background
    which uses the gated orchestrator.finalize() path.

    Raises RuntimeError if called — any caller should use the orchestrator path.
    """
    raise RuntimeError(
        "generate_handoff_report_background is deprecated. "
        "Use _generate_orchestrator_report_background which goes through the unified safety gate."
    )


async def _generate_orchestrator_report_background(
    session_id: str,
    orch_session,
    session_metadata: dict,
):
    """Background task to save orchestrator audit trace and SBAR report.

    This runs AFTER the TwiML response is returned to Twilio, so it
    does not block the caller experience.

    Resilience: each phase (finalize → persist → write → upload) is
    independently wrapped so a failure in one phase does not prevent
    subsequent phases from running.
    """
    finalize = None
    report_json_path = None
    report_txt_path = None

    try:
        logger.info(f"[BACKGROUND] Saving orchestrator report for session {session_id}")

        # ── Phase 1: Finalize ────────────────────────────────────────────
        if orch_session.finalize_output is None:
            try:
                if orch_session.turn_count <= 2:
                    from src.orchestrator.validators import safe_finalize_default

                    logger.info(
                        f"[BACKGROUND] Skipping LLM finalize for early-turn escalation "
                        f"(turn_count={orch_session.turn_count}) — using safe default"
                    )
                    orch_session.finalize_output = safe_finalize_default()
                else:
                    orchestrator = get_orchestrator()
                    await orchestrator.finalize(orch_session)
            except Exception as fin_exc:
                logger.error(
                    f"[BACKGROUND] LLM finalize failed — using safe default: {fin_exc}",
                    exc_info=True,
                )
                from src.orchestrator.validators import safe_finalize_default

                orch_session.finalize_output = safe_finalize_default()

        finalize = orch_session.finalize_output

        # ── Phase 2: Persist session ─────────────────────────────────────
        try:
            repo = get_session_repository()
            repo.persist_session(orch_session)
        except Exception as persist_exc:
            logger.error(
                f"[BACKGROUND] Session persist failed (non-fatal): {persist_exc}",
                exc_info=True,
            )

        # ── Phase 3: Build structured report ─────────────────────────────
        structured = {
            "patient": {
                "name": session_metadata.get("patient_name", "Unknown"),
                "age": session_metadata.get("patient_age", "Unknown"),
                "sex": session_metadata.get("patient_sex", "Unknown"),
            },
            "chief_complaint": orch_session.intake_state.chief_complaint
            or "Not documented",
            "disposition": {
                "level": finalize.disposition.value if finalize else "HUMAN_REVIEW",
                "reasoning": finalize.disposition_reasoning
                if finalize
                else "Report generation unavailable",
            },
            "intake_state": orch_session.intake_state.model_dump(exclude_none=True),
            "safety_flags": [f.model_dump() for f in orch_session.safety_flags],
            "audit_trace": {
                "entries": [
                    e.model_dump(mode="json") for e in orch_session.audit_trace.entries
                ],
                "deterministic_rules_triggered": orch_session.audit_trace.deterministic_rules_triggered,
            },
        }

        sbar_text = finalize.sbar_report if finalize else "SBAR generation unavailable."

        patient_name = session_metadata.get("patient_name", "")
        disposition = finalize.disposition.value if finalize else "HUMAN_REVIEW"

        generate_report_filename(
            session_id=session_id,
            patient_name=patient_name,
            disposition=disposition,
        )
        report_base = generate_report_path(
            reports_dir=REPORTS_DIR,
            session_id=session_id,
            patient_name=patient_name,
            disposition=disposition,
        )
        report_json_path = report_base.with_suffix(".json")
        report_txt_path = report_base.with_suffix(".txt")

        # ── Phase 4: Write local files ───────────────────────────────────
        try:
            with open(report_json_path, "w") as f:
                json.dump(structured, f, indent=2, default=str)

            # Apply PHI masking to file-written text when STORE_PHI is disabled
            sbar_for_file = sbar_text if STORE_PHI else mask_phi(sbar_text)

            with open(report_txt_path, "w") as f:
                f.write("Triage Handoff Report (Orchestrator)\n")
                f.write(f"Session ID: {session_id}\n")
                f.write(f"Generated: {datetime.now().isoformat()}\n")
                f.write(f"{'=' * 80}\n\n")
                f.write(sbar_for_file)
                if finalize and finalize.safety_net_instructions:
                    instructions = chr(10).join(finalize.safety_net_instructions)
                    if not STORE_PHI:
                        instructions = mask_phi(instructions)
                    f.write(f"\n\nSafety-Net Instructions:\n{instructions}")

            logger.info(f"[BACKGROUND] Orchestrator report saved: {report_json_path}")
        except Exception as write_exc:
            logger.error(
                f"[BACKGROUND] Local file write failed: {write_exc}", exc_info=True
            )

        # ── Phase 5: Upload to Azure Blob Storage ────────────────────────
        if report_json_path and report_json_path.exists():
            try:
                blob_urls = upload_reports_to_blob(
                    report_json_path, report_txt_path, REPORTS_DIR
                )
                if blob_urls.get("json_url"):
                    logger.info(
                        f"[BACKGROUND] Reports uploaded to blob storage: "
                        f"{blob_urls.get('json_url')}"
                    )
                else:
                    logger.warning(
                        "[BACKGROUND] Blob upload returned no URL — "
                        "check AZURE_STORAGE_CONNECTION_STRING"
                    )
            except Exception as blob_exc:
                logger.warning(
                    f"[BACKGROUND] Blob upload failed (non-fatal): {blob_exc}",
                    exc_info=True,
                )
        else:
            logger.warning(
                "[BACKGROUND] Skipping blob upload — local files not written"
            )

    except Exception as e:
        logger.error(
            f"[BACKGROUND] Failed to generate orchestrator report: {e}", exc_info=True
        )


# Single-word responses that STT commonly produces when a caller
# greets or acknowledges the system rather than saying their name.
_NAME_BLOCKLIST: frozenset[str] = frozenset(
    {
        # Honorifics / titles
        "sir",
        "maam",
        "miss",
        "mister",
        "mr",
        "mrs",
        "ms",
        "dr",
        # Greetings / filler
        "hello",
        "hi",
        "hey",
        "yo",
        "um",
        "uh",
        "hmm",
        "hm",
        # Affirmations / negations
        "yes",
        "no",
        "ok",
        "okay",
        "nope",
        "yep",
        "yeah",
        "nah",
        "sure",
        # Courtesy filler
        "thanks",
        "thank",
        "please",
        "sorry",
        "excuse",
        "pardon",
        # Generic question / filler words
        "what",
        "who",
        "well",
    }
)


def _looks_like_name(text: str) -> bool:
    """Return True if *text* plausibly represents a person's name.

    Rejects obvious STT garbage such as sentence fragments, excessive word
    counts, or well-known single-word non-names (e.g.
    ``'Ma\'am.'``).

    Note: commas are NOT used as a rejection signal because Twilio STT
    frequently inserts commas into names ("Test Patient, Ten").
    The 5-word max + 80% alpha ratio is sufficient to reject sentences.
    """
    stripped = text.strip().rstrip(".")  # STT often appends a trailing period
    if not stripped:
        return False
    words = stripped.split()
    # More than 5 words is almost certainly not a name
    if len(words) > 5:
        return False
    # At least 80 % of non-space characters should be alphabetic or name-safe
    core_chars = [c for c in stripped if not c.isspace()]
    if not core_chars:
        return False
    alpha_count = sum(1 for c in core_chars if c.isalpha() or c in "-',")
    if alpha_count / len(core_chars) < 0.80:
        return False
    # Reject single-word salutations / honorifics the STT engine commonly
    # produces when a caller says "Ma'am", "Yes", "Hello", etc.
    if len(words) == 1:
        normalized = words[0].lower().replace("'", "")
        if normalized in _NAME_BLOCKLIST:
            return False
    return True


# Stage definitions for scripted intake
STAGE_GREETING = "GREET"
STAGE_NAME = "NAME"
STAGE_AGE = "AGE"
STAGE_SEX = "SEX"
STAGE_CHIEF_COMPLAINT = "CHIEF_COMPLAINT"
STAGE_DYNAMIC = "DYNAMIC"

# Maximum name re-prompts before accepting whatever was given
_MAX_NAME_RETRIES = 3

# Sex normalisation — maps common STT transcriptions to canonical values.
# Twilio STT on phone audio frequently mishears "male" as "mail", "mel",
# "men", etc.  This map + prefix stripping ("I'm male" → "male") ensures
# robust recognition.
_SEX_NORMALISATION: dict[str, str] = {
    # Male
    "male": "male",
    "mail": "male",
    "mel": "male",
    "men": "male",
    "man": "male",
    "mael": "male",
    "mayo": "male",
    "mell": "male",
    "m": "male",
    # Female
    "female": "female",
    "femail": "female",
    "fema": "female",
    "women": "female",
    "woman": "female",
    "f": "female",
    # Prefer not to say
    "prefer not to say": "prefer not to say",
    "rather not say": "prefer not to say",
    "prefer not": "prefer not to say",
    "i'd rather not": "prefer not to say",
    "none": "prefer not to say",
}


def _normalise_sex(raw: str) -> str:
    """Normalise STT sex response to 'male', 'female', or 'prefer not to say'.

    Handles prefix stripping ('I am male' → 'male'), common STT confusions
    ('mail' → 'male'), and single-letter answers ('m' → 'male').
    Falls back to the raw lowered text if no match is found.
    """
    cleaned = raw.strip().lower().rstrip(".!?,;: ")
    # Strip common conversational prefixes
    for prefix in (
        "i am ",
        "i'm ",
        "my sex is ",
        "my biological sex is ",
        "it's ",
        "its ",
    ):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    return _SEX_NORMALISATION.get(cleaned, cleaned)


# Voice used for all TwiML speech — Polly Ruth is calm and clear, ideal for healthcare
_TTS_VOICE = "Polly.Ruth-Neural"

# ---------------------------------------------------------------------------
# Pending orchestrator tasks — background LLM processing with typing sounds
# ---------------------------------------------------------------------------
# Keyed by CallSid → asyncio.Task that resolves to (result_dict, session).
# Single-worker in-memory storage (same pattern as session store).
_pending_turns: dict[str, asyncio.Task] = {}


def _get_azure_voice() -> str:
    """Get the configured Azure TTS voice name."""
    from src.config import AZURE_TTS_VOICE

    return AZURE_TTS_VOICE


async def generate_twiml_say(text: str) -> str:
    """Generate TwiML <Say> or <Play> with Azure TTS (fallback to Polly)."""
    audio_url = await text_to_speech_url(text, _get_azure_voice())
    if audio_url:
        return f"<Play>{saxutils.escape(audio_url)}</Play>"
    return f'<Say voice="{_TTS_VOICE}">{saxutils.escape(text)}</Say>'


async def generate_twiml_gather(
    prompt: str,
    action_url: str,
    timeout: int = 6,
    hints: str | None = None,
    speech_timeout: str = "3",
) -> str:
    """Generate TwiML with <Gather> for speech input.

    Uses Azure TTS <Play> when available, falls back to Polly <Say>.
    Uses enhanced phone_call speech model for better noise rejection and
    accuracy on telephony audio.

    Args:
        prompt: Text to speak inside the Gather.
        action_url: URL that receives the speech result.
        timeout: Seconds of silence before Gather gives up (no speech at all).
        hints: Comma-separated speech hints for STT biasing (e.g. "male,female").
        speech_timeout: Seconds of silence after speech begins before ending.
            "auto" lets Twilio decide; "3" is the default (up from "2" to
            accommodate natural pauses, confused callers, and elderly speakers).
    """
    escaped_prompt = saxutils.escape(prompt)
    audio_url = await text_to_speech_url(prompt, _get_azure_voice())
    hints_attr = f' hints="{saxutils.escape(hints)}"' if hints else ""

    if audio_url:
        escaped_url = saxutils.escape(audio_url)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" speechTimeout="{speech_timeout}" timeout="{timeout}" action="{action_url}" method="POST" speechModel="phone_call" enhanced="true"{hints_attr}>
        <Play>{escaped_url}</Play>
    </Gather>
    <Redirect method="POST">{action_url}</Redirect>
</Response>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" speechTimeout="{speech_timeout}" timeout="{timeout}" action="{action_url}" method="POST" speechModel="phone_call" enhanced="true"{hints_attr}>
        <Say voice="{_TTS_VOICE}">{escaped_prompt}</Say>
    </Gather>
    <Redirect method="POST">{action_url}</Redirect>
</Response>"""


async def generate_twiml_say_and_hangup(text: str) -> str:
    """Generate TwiML that says/plays something and hangs up"""
    escaped_text = saxutils.escape(text)
    audio_url = await text_to_speech_url(text, _get_azure_voice())

    if audio_url:
        escaped_url = saxutils.escape(audio_url)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{escaped_url}</Play>
    <Hangup/>
</Response>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="{_TTS_VOICE}">{escaped_text}</Say>
    <Hangup/>
</Response>"""


async def generate_twiml_handoff(text: str) -> str:
    """Speak a closing message then transfer or hang up.

    If ``NURSE_TRANSFER_NUMBER`` is set (e.g. ``+18005551234``), the call is
    transferred via Twilio ``<Dial>`` so the caller reaches the nurse queue
    without dead air.  Falls back to ``<Hangup>`` when unconfigured.
    """
    from src.config import NURSE_TRANSFER_NUMBER

    escaped_text = saxutils.escape(text)
    audio_url = await text_to_speech_url(text, _get_azure_voice())

    if NURSE_TRANSFER_NUMBER:
        num = saxutils.escape(NURSE_TRANSFER_NUMBER)
        if audio_url:
            speech_tag = f"    <Play>{saxutils.escape(audio_url)}</Play>"
        else:
            speech_tag = f'    <Say voice="{_TTS_VOICE}">{escaped_text}</Say>'
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f"{speech_tag}\n"
            f"    <Dial>{num}</Dial>\n"
            "</Response>"
        )
    return await generate_twiml_say_and_hangup(text)


@router.post("/incoming")
async def handle_incoming_call(request: Request, CallSid: str = Form(...)):
    """
    Handle incoming Twilio call — create session via SessionRepository
    and start scripted intake.
    """
    try:
        logger.info(f"[TWILIO] Incoming call: {CallSid}")

        repo = get_session_repository()
        session = repo.create_session(call_sid=CallSid)

        # Track scripted intake stage
        session.channel_metadata["stage"] = STAGE_GREETING

        # Generate greeting — split into non-interruptible preamble +
        # Gather for the name question.  This prevents barge-in from
        # cutting off the legal disclaimer and ensures Jenny is used
        # (long combined text was timing out TTS on cold start).
        greeting = (
            "Hello, this is Astra, a clinical triage assistant. "
            "This is a decision-support tool, not a diagnostic service. "
            "If you are experiencing a life-threatening emergency, "
            "please hang up and call 9 1 1 immediately. "
        )

        # Store greeting in conversation
        session.conversation.append(ConversationTurn(role="assistant", text=greeting))

        # Move to NAME stage
        session.channel_metadata["stage"] = STAGE_NAME
        first_question = "I will be asking you a series of questions to help assess your symptoms. Can I start with your full name?"
        session.conversation.append(
            ConversationTurn(role="assistant", text=first_question)
        )

        repo.persist_session(session)

        # TTS: generate both audio URLs concurrently
        greeting_url, question_url = await asyncio.gather(
            text_to_speech_url(greeting, _get_azure_voice()),
            text_to_speech_url(first_question, _get_azure_voice()),
        )

        # Build TwiML: Play greeting (non-interruptible), then
        # Gather with the name question (allows caller to respond).
        greeting_tag = (
            f"<Play>{saxutils.escape(greeting_url)}</Play>"
            if greeting_url
            else f'<Say voice="{_TTS_VOICE}">{saxutils.escape(greeting)}</Say>'
        )
        if question_url:
            question_tag = f"<Play>{saxutils.escape(question_url)}</Play>"
        else:
            question_tag = (
                f'<Say voice="{_TTS_VOICE}">{saxutils.escape(first_question)}</Say>'
            )

        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    {greeting_tag}
    <Gather input="speech" speechTimeout="3" timeout="8" action="/api/v1/voice/gather" method="POST" speechModel="phone_call" enhanced="true">
        {question_tag}
    </Gather>
    <Redirect method="POST">/api/v1/voice/gather</Redirect>
</Response>"""

        logger.info(f"[TWILIO] Session {session.session_id} started for call {CallSid}")
        return Response(content=twiml, media_type="application/xml")

    except Exception as e:
        logger.error(f"[TWILIO] Error in incoming call: {e}", exc_info=True)
        error_twiml = await generate_twiml_say_and_hangup(
            "Sorry, we encountered a technical error. Please try again later."
        )
        return Response(content=error_twiml, media_type="application/xml")


@router.post("/gather")
async def handle_gather(
    request: Request,
    background_tasks: BackgroundTasks,
    CallSid: str = Form(...),
    SpeechResult: Optional[str] = Form(None),
):
    """
    Handle Twilio <Gather> results — all state in OrchestratorSession via SessionRepository.
    """
    try:
        logger.info(
            f"[TWILIO] Gather from {CallSid}: speech_len={len(SpeechResult) if SpeechResult else 0}"
        )

        repo = get_session_repository()
        session = repo.load_session_by_call(CallSid)

        if not session:
            logger.error(f"[TWILIO] No session found for call {CallSid}")
            twiml = await generate_twiml_say_and_hangup(
                "Sorry, your session has expired. Please call back."
            )
            return Response(content=twiml, media_type="application/xml")

        session_id = session.session_id

        # Handle empty speech result
        # Speak EXACTLY one apology sentence and re-run the same stage question
        # already embedded in the next <Gather>.  Do NOT append the stage
        # question here — that would double-prompt the caller.
        if not SpeechResult or SpeechResult.strip() == "":
            logger.warning(f"[TWILIO] Empty speech result from {CallSid}")
            twiml = await generate_twiml_gather(
                "Sorry, I didn't catch that. Can you please repeat your answer?",
                "/api/v1/voice/gather",
            )
            return Response(content=twiml, media_type="application/xml")

        # Store patient's answer in conversation
        session.conversation.append(
            ConversationTurn(role="caller", text=SpeechResult.strip())
        )

        # Get current stage
        current_stage = session.channel_metadata.get("stage", STAGE_NAME)
        logger.info(f"[TWILIO] Session {session_id} current stage: {current_stage}")

        # Process scripted intake stages
        if current_stage == STAGE_NAME:
            name_input = SpeechResult.strip()
            name_retries = session.channel_metadata.get("name_retries", 0)
            if not _looks_like_name(name_input):
                name_retries += 1
                session.channel_metadata["name_retries"] = name_retries
                if name_retries >= _MAX_NAME_RETRIES:
                    # Accept whatever was given after max retries to avoid
                    # caller frustration and call abandonment.
                    logger.warning(
                        f"[TWILIO] Name accepted after {name_retries} failed "
                        f"validations: '{mask_phi(name_input[:80])}'"
                    )
                    session.intake_state.caller_name = name_input
                    session.channel_metadata["stage"] = STAGE_AGE
                    next_question = "Thank you. What is your age?"
                    session.conversation.append(
                        ConversationTurn(role="assistant", text=next_question)
                    )
                    repo.persist_session(session)
                    twiml = await generate_twiml_gather(
                        next_question, "/api/v1/voice/gather"
                    )
                    return Response(content=twiml, media_type="application/xml")
                # STT produced garbage — re-prompt without advancing the stage
                logger.warning(
                    f"[TWILIO] Name rejected (attempt {name_retries}): "
                    f"'{mask_phi(name_input[:80])}'"
                )
                repo.persist_session(session)
                twiml = await generate_twiml_gather(
                    "I'm sorry, I didn't quite catch your name. "
                    "Could you please say just your first and last name?",
                    "/api/v1/voice/gather",
                )
                return Response(content=twiml, media_type="application/xml")
            session.intake_state.caller_name = name_input
            session.channel_metadata["stage"] = STAGE_AGE
            next_question = "Thank you. What is your age?"
            session.conversation.append(
                ConversationTurn(role="assistant", text=next_question)
            )
            repo.persist_session(session)
            twiml = await generate_twiml_gather(next_question, "/api/v1/voice/gather")
            return Response(content=twiml, media_type="application/xml")

        elif current_stage == STAGE_AGE:
            try:
                session.intake_state.caller_age = int(SpeechResult.strip())
            except (ValueError, TypeError):
                session.channel_metadata["patient_age_raw"] = SpeechResult.strip()
            session.channel_metadata["stage"] = STAGE_SEX
            next_question = "And what is your biological sex? Please say male, female, or prefer not to say."
            session.conversation.append(
                ConversationTurn(role="assistant", text=next_question)
            )
            repo.persist_session(session)
            twiml = await generate_twiml_gather(
                next_question,
                "/api/v1/voice/gather",
                hints="male,female,prefer not to say",
            )
            return Response(content=twiml, media_type="application/xml")

        elif current_stage == STAGE_SEX:
            session.intake_state.caller_sex = _normalise_sex(SpeechResult)
            session.channel_metadata["stage"] = STAGE_CHIEF_COMPLAINT
            next_question = "Thank you. Now, what brings you in today? Please describe your main symptom or concern."
            session.conversation.append(
                ConversationTurn(role="assistant", text=next_question)
            )
            repo.persist_session(session)
            twiml = await generate_twiml_gather(
                next_question,
                "/api/v1/voice/gather",
                timeout=10,
                speech_timeout="auto",
            )
            return Response(content=twiml, media_type="application/xml")

        elif current_stage == STAGE_CHIEF_COMPLAINT:
            chief_complaint = SpeechResult.strip()
            session.intake_state.chief_complaint = chief_complaint
            session.channel_metadata["stage"] = STAGE_DYNAMIC
            logger.info(f"[TWILIO] Session {session_id} entering DYNAMIC stage")
            logger.info(f"[TWILIO] Chief complaint stored (len={len(chief_complaint)})")
            repo.persist_session(session)
            # Fall through to dynamic processing

        # DYNAMIC stage — multi-agent orchestrator
        if session.channel_metadata.get("stage") == STAGE_DYNAMIC:
            # Kick off the orchestrator in the background and return
            # typing sounds immediately so the caller doesn't sit in silence.
            speech_text = SpeechResult.strip()

            async def _run_turn(sess, text):
                """Background coroutine: run orchestrator + persist."""
                import time as _t

                _t0 = _t.monotonic()
                orch = get_orchestrator()
                res = await orch.process_turn(sess, text)
                _ms = (_t.monotonic() - _t0) * 1000
                logger.info(
                    f"[TWILIO] Orchestrator completed in {_ms:.0f}ms for session {sess.session_id}"
                )
                r = get_session_repository()
                r.persist_session(sess)
                return res

            task = asyncio.create_task(_run_turn(session, speech_text))
            _pending_turns[CallSid] = task
            logger.info(f"[TWILIO] Started background orchestrator for {CallSid}")

            # Return typing sounds → poll via /thinking
            typing_url = "/api/v1/voice/audio/typing.wav"
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{typing_url}</Play>
    <Redirect method="POST">/api/v1/voice/thinking</Redirect>
</Response>"""
            return Response(content=twiml, media_type="application/xml")

        # Should not reach here
        logger.error(f"[TWILIO] Unexpected state for session {session_id}")
        twiml = await generate_twiml_say_and_hangup(
            "Thank you for answering our questions. All information has been securely recorded and will be passed on to a nurse for review. A nurse will contact you promptly. If your symptoms worsen, please go to the emergency room or call emergency services."
        )
        return Response(content=twiml, media_type="application/xml")

    except Exception as e:
        logger.error(f"[TWILIO] Error in gather: {e}", exc_info=True)
        error_twiml = await generate_twiml_say_and_hangup(
            "Sorry, we encountered an error. Please try again later."
        )
        return Response(content=error_twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# /thinking — poll loop that plays typing sounds until the LLM is ready
# ---------------------------------------------------------------------------


@router.post("/thinking")
async def handle_thinking(
    request: Request,
    background_tasks: BackgroundTasks,
    CallSid: str = Form(...),
):
    """Poll endpoint: plays typing sounds while the orchestrator runs.

    When the background orchestrator task completes, this endpoint returns
    the actual TwiML response (Gather / Say+Hangup / Handoff).
    While still processing, it plays another cycle of typing sounds and
    redirects back to itself.
    """
    try:
        task = _pending_turns.get(CallSid)

        # No pending task — shouldn't happen, but recover gracefully
        if task is None:
            logger.warning(
                f"[TWILIO] /thinking called with no pending task for {CallSid}"
            )
            twiml = await generate_twiml_gather(
                "Sorry about the wait. Can you repeat that for me?",
                "/api/v1/voice/gather",
                timeout=8,
                speech_timeout="auto",
            )
            return Response(content=twiml, media_type="application/xml")

        # Task still running — play more typing sounds and loop back
        if not task.done():
            typing_url = "/api/v1/voice/audio/typing.wav"
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{typing_url}</Play>
    <Redirect method="POST">/api/v1/voice/thinking</Redirect>
</Response>"""
            return Response(content=twiml, media_type="application/xml")

        # ── Task complete — deliver the result ────────────────────────────
        del _pending_turns[CallSid]

        # Check for exceptions
        if task.cancelled():
            logger.error(f"[TWILIO] Orchestrator task cancelled for {CallSid}")
            twiml = await generate_twiml_say_and_hangup(
                "I'm sorry, something went wrong. Please call back and we'll help you."
            )
            return Response(content=twiml, media_type="application/xml")

        exc = task.exception()
        if exc is not None:
            logger.error(
                f"[TWILIO] Orchestrator task failed for {CallSid}: {exc}", exc_info=exc
            )
            twiml = await generate_twiml_say_and_hangup(
                "I'm sorry, something went wrong. Please call back and we'll help you."
            )
            return Response(content=twiml, media_type="application/xml")

        result = task.result()
        action = result["action"]
        spoken_message = result["message"]

        # Load session for finalization actions
        repo = get_session_repository()
        session = repo.load_session_by_call(CallSid)
        session_id = session.session_id if session else CallSid

        import time as _time

        _t_tts = _time.monotonic()

        if action == "escalate":
            logger.info(f"[TWILIO] Deterministic escalation for session {session_id}")
            if session:
                session.is_finalized = True
                repo.persist_session(session)
                background_tasks.add_task(
                    _generate_orchestrator_report_background,
                    session_id=session_id,
                    orch_session=session,
                    session_metadata=_build_session_metadata(session),
                )
            twiml = await generate_twiml_say_and_hangup(spoken_message)
            return Response(content=twiml, media_type="application/xml")

        elif action == "finalize":
            logger.info(f"[TWILIO] Finalizing session {session_id}")
            if session:
                session.is_finalized = True
                repo.persist_session(session)
                background_tasks.add_task(
                    _generate_orchestrator_report_background,
                    session_id=session_id,
                    orch_session=session,
                    session_metadata=_build_session_metadata(session),
                )
            twiml = await generate_twiml_handoff(spoken_message)
            return Response(content=twiml, media_type="application/xml")

        else:
            # action == "ask" — continue with next question
            twiml = await generate_twiml_gather(
                spoken_message,
                "/api/v1/voice/gather",
                timeout=8,
                speech_timeout="auto",
            )
            _tts_ms = (_time.monotonic() - _t_tts) * 1000
            logger.info(f"[TWILIO] TTS={_tts_ms:.0f}ms for session {session_id}")
            return Response(content=twiml, media_type="application/xml")

    except Exception as e:
        logger.error(f"[TWILIO] Error in /thinking: {e}", exc_info=True)
        # Clean up pending task
        _pending_turns.pop(CallSid, None)
        error_twiml = await generate_twiml_say_and_hangup(
            "Sorry, we encountered an error. Please try again later."
        )
        return Response(content=error_twiml, media_type="application/xml")


def _build_session_metadata(session) -> dict:
    """Extract metadata dict from OrchestratorSession for report generation."""
    return {
        "patient_name": session.intake_state.caller_name or "Unknown",
        "patient_age": str(session.intake_state.caller_age)
        if session.intake_state.caller_age
        else "Unknown",
        "patient_sex": session.intake_state.caller_sex or "Unknown",
        "chief_complaint": session.intake_state.chief_complaint or "Unknown",
    }


def _get_stage_question(stage: str) -> str:
    """Get the question for a given stage"""
    questions = {
        STAGE_NAME: "What is your full name?",
        STAGE_AGE: "What is your age?",
        STAGE_SEX: "What is your biological sex? Please say male, female, or prefer not to say.",
        STAGE_CHIEF_COMPLAINT: "What brings you in today? Please describe your main symptom or concern.",
        STAGE_DYNAMIC: "Can you tell me more about your symptoms?",
    }
    return questions.get(stage, "Can you tell me more?")
