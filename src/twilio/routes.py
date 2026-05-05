"""
Twilio Voice Routes — Phase 5 SaaS Infrastructure

All session state goes through SessionRepository → StorageFactory.
All LLM calls go through Orchestrator → GuardedLLM → SafetyGate.
Twilio signature validation enforced via router-level dependency.
"""

import asyncio
import logging
import json
from datetime import UTC, datetime
from pathlib import Path
from xml.sax import saxutils
from fastapi import APIRouter, Depends, Request, Form, BackgroundTasks
from fastapi.responses import Response
from typing import Optional

from src.storage.session_repository import get_session_repository
from src.orchestrator.orchestrator import get_orchestrator
from src.orchestrator.schemas import ConversationTurn, OrchestratorSession
from src.platform.workflows.registry import ensure_default_workflows_registered
from src.platform.workflows.router import (
    get_workflow_engine,
    get_workflow_route_resolver,
)
from src.platform.workflows.schemas import (
    ScriptedIntakeDefinition,
    ScriptedStageDefinition,
    WorkflowContext,
    WorkflowFinalResult,
    WorkflowInput,
    WorkflowTurnResult,
)
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
        try:
            _run_post_call_extraction(orch_session)
        except Exception as extraction_exc:
            logger.warning(
                f"[BACKGROUND] Post-call extraction failed (non-fatal): {extraction_exc}",
                exc_info=True,
            )

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
    "prefer not to say": "unknown",
    "rather not say": "unknown",
    "prefer not": "unknown",
    "i'd rather not": "unknown",
    "none": "unknown",
}


def _normalise_sex(raw: str) -> str:
    """Normalise STT sex response to 'male', 'female', or 'unknown'.

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


def _normalise_yes_no(raw: str) -> str:
    cleaned = raw.strip().lower().rstrip(".!?,;: ")
    for prefix in ("yes ", "yeah ", "yep ", "no ", "nope "):
        if cleaned.startswith(prefix):
            cleaned = prefix.strip()
            break
    if cleaned in {"yes", "yeah", "yep", "sure", "ok", "okay", "permission granted"}:
        return "yes"
    if cleaned in {"no", "nope", "do not", "don't", "do not enter"}:
        return "no"
    return cleaned


def _normalise_phone(raw: str) -> str:
    cleaned = raw.strip()
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if cleaned.startswith("+") and digits:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return cleaned


# Voice used for all TwiML speech — Polly Ruth is calm and clear, ideal for healthcare
_TTS_VOICE = "Polly.Ruth-Neural"

# ---------------------------------------------------------------------------
# Pending orchestrator tasks — background LLM processing with typing sounds
# ---------------------------------------------------------------------------
# Keyed by CallSid → asyncio.Task that resolves to (result_dict, session).
# Single-worker in-memory storage (same pattern as session store).
_pending_turns: dict[str, tuple[asyncio.Task, object]] = {}


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


async def _generate_initial_scripted_twiml(
    intro_text: str | None,
    first_stage: ScriptedStageDefinition,
    action_url: str,
) -> str:
    """Generate first scripted-intake TwiML from workflow stage metadata."""

    prompt = _stage_prompt(first_stage)
    if not intro_text:
        return await generate_twiml_gather(
            prompt,
            action_url,
            timeout=first_stage.timeout_seconds,
            hints=first_stage.hints,
            speech_timeout=first_stage.speech_timeout,
        )

    intro_url, question_url = await asyncio.gather(
        text_to_speech_url(intro_text, _get_azure_voice()),
        text_to_speech_url(prompt, _get_azure_voice()),
    )
    intro_tag = (
        f"<Play>{saxutils.escape(intro_url)}</Play>"
        if intro_url
        else f'<Say voice="{_TTS_VOICE}">{saxutils.escape(intro_text)}</Say>'
    )
    question_tag = (
        f"<Play>{saxutils.escape(question_url)}</Play>"
        if question_url
        else f'<Say voice="{_TTS_VOICE}">{saxutils.escape(prompt)}</Say>'
    )
    hints_attr = (
        f' hints="{saxutils.escape(first_stage.hints)}"' if first_stage.hints else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    {intro_tag}
    <Gather input="speech" speechTimeout="{first_stage.speech_timeout}" timeout="{first_stage.timeout_seconds}" action="{action_url}" method="POST" speechModel="phone_call" enhanced="true"{hints_attr}>
        {question_tag}
    </Gather>
    <Redirect method="POST">{action_url}</Redirect>
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


def _get_workflow_for_session(session: OrchestratorSession):
    registry = ensure_default_workflows_registered()
    return registry.get(session.workflow_id or "healthcare_triage_v1")


def _get_scripted_intake_for_session(
    session: OrchestratorSession,
) -> ScriptedIntakeDefinition | None:
    workflow = _get_workflow_for_session(session)
    return workflow.get_scripted_intake_definition()


def _initialize_scripted_intake(
    session: OrchestratorSession,
    intake: ScriptedIntakeDefinition,
) -> ScriptedStageDefinition | None:
    stages = list(intake.stages or [])
    if not stages:
        session.channel_metadata["stage"] = intake.completion_stage_id
        session.channel_metadata["scripted_intake"] = {
            "workflow_id": session.workflow_id,
            "current_index": None,
            "current_stage_id": intake.completion_stage_id,
            "fields": {},
            "attempts": {},
            "completed": True,
        }
        return None

    first_stage = stages[0]
    session.channel_metadata["stage"] = first_stage.stage_id
    session.channel_metadata["scripted_intake"] = {
        "workflow_id": session.workflow_id,
        "current_index": 0,
        "current_stage_id": first_stage.stage_id,
        "fields": {},
        "attempts": {},
        "completed": False,
    }
    return first_stage


def _current_scripted_stage(
    session: OrchestratorSession,
    intake: ScriptedIntakeDefinition | None,
) -> ScriptedStageDefinition | None:
    if intake is None or not intake.stages:
        return None
    current_stage_id = session.channel_metadata.get("stage")
    for stage in intake.stages:
        if stage.stage_id == current_stage_id:
            return stage
    scripted = session.channel_metadata.get("scripted_intake") or {}
    index = scripted.get("current_index")
    if isinstance(index, int) and 0 <= index < len(intake.stages):
        return intake.stages[index]
    return None


def _stage_prompt(stage: ScriptedStageDefinition) -> str:
    return stage.prompt_text or stage.prompt


def _stage_field_type(stage: ScriptedStageDefinition) -> str:
    return (stage.field_type or stage.expected_answer_type or "free_text").lower()


def _stage_store_key(stage: ScriptedStageDefinition) -> str:
    return stage.store_as or stage.field_name


def _record_scripted_field(
    session: OrchestratorSession,
    stage: ScriptedStageDefinition,
    value,
) -> None:
    scripted = session.channel_metadata.setdefault("scripted_intake", {})
    fields = scripted.setdefault("fields", {})
    store_key = _stage_store_key(stage)
    fields[store_key] = value

    # Compatibility while sessions are still backed by OrchestratorSession:
    # healthcare intake fields live on intake_state, while future verticals
    # live in scripted_intake.fields.
    if hasattr(session.intake_state, store_key):
        setattr(session.intake_state, store_key, value)


def _parse_scripted_answer(
    session: OrchestratorSession,
    stage: ScriptedStageDefinition,
    speech_text: str,
) -> tuple[bool, object, str | None]:
    raw = speech_text.strip()
    field_type = _stage_field_type(stage)
    store_key = _stage_store_key(stage)

    if store_key == "caller_name" or field_type == "name":
        if _looks_like_name(raw):
            return True, raw, None
        return (
            False,
            None,
            (
                stage.reprompt_text
                or "I'm sorry, I didn't quite catch your name. "
                "Could you please say just your first and last name?"
            ),
        )

    if field_type in {"integer", "number"}:
        try:
            return True, int(raw), None
        except (ValueError, TypeError):
            session.channel_metadata[f"{store_key}_raw"] = raw
            return True, None, None

    if store_key == "caller_sex":
        return True, _normalise_sex(raw), None

    if field_type in {"enum", "choice"}:
        normalized = _normalise_yes_no(raw)
        allowed_values = stage.allowed_values or []
        if allowed_values:
            allowed_map = {value.lower(): value for value in allowed_values}
            if normalized.lower() in allowed_map:
                return True, allowed_map[normalized.lower()], None
            for allowed in allowed_values:
                if allowed.lower() in normalized.lower():
                    return True, allowed, None
        return True, normalized, None

    if field_type == "phone":
        return True, _normalise_phone(raw), None

    return True, raw, None


def _increment_scripted_attempt(
    session: OrchestratorSession,
    stage: ScriptedStageDefinition,
) -> int:
    scripted = session.channel_metadata.setdefault("scripted_intake", {})
    attempts = scripted.setdefault("attempts", {})
    stage_id = stage.stage_id
    attempts[stage_id] = int(attempts.get(stage_id, 0)) + 1
    return attempts[stage_id]


def _advance_scripted_intake(
    session: OrchestratorSession,
    intake: ScriptedIntakeDefinition,
    current_stage: ScriptedStageDefinition,
) -> ScriptedStageDefinition | None:
    scripted = session.channel_metadata.setdefault("scripted_intake", {})
    stages = list(intake.stages or [])
    current_index = stages.index(current_stage)
    next_index = current_index + 1
    if next_index >= len(stages):
        scripted["current_index"] = None
        scripted["current_stage_id"] = intake.completion_stage_id
        scripted["completed"] = True
        session.channel_metadata["stage"] = intake.completion_stage_id
        return None

    next_stage = stages[next_index]
    scripted["current_index"] = next_index
    scripted["current_stage_id"] = next_stage.stage_id
    session.channel_metadata["stage"] = next_stage.stage_id
    return next_stage


async def _handle_scripted_stage_response(
    session: OrchestratorSession,
    intake: ScriptedIntakeDefinition,
    stage: ScriptedStageDefinition,
    speech_text: str,
):
    success, parsed_value, reprompt = _parse_scripted_answer(
        session,
        stage,
        speech_text,
    )
    if not success:
        attempts = _increment_scripted_attempt(session, stage)
        max_attempts = stage.max_attempts or _MAX_NAME_RETRIES
        if attempts < max_attempts:
            logger.warning(
                "[TWILIO] Scripted intake rejected stage %s attempt %s",
                stage.stage_id,
                attempts,
            )
            return stage, reprompt or _stage_prompt(stage)
        logger.warning(
            "[TWILIO] Scripted intake accepted stage %s after %s failed validations: %s",
            stage.stage_id,
            attempts,
            mask_phi(speech_text[:80]),
        )
        _record_scripted_field(session, stage, speech_text.strip())
    else:
        if parsed_value is not None:
            _record_scripted_field(session, stage, parsed_value)

    next_stage = _advance_scripted_intake(session, intake, stage)
    return next_stage, None


async def _prompt_for_scripted_stage(stage: ScriptedStageDefinition) -> str:
    return await generate_twiml_gather(
        _stage_prompt(stage),
        "/api/v1/voice/gather",
        timeout=stage.timeout_seconds,
        hints=stage.hints,
        speech_timeout=stage.speech_timeout,
    )


@router.post("/incoming")
async def handle_incoming_call(
    request: Request,
    CallSid: str = Form(...),
    To: Optional[str] = Form(None),
):
    """
    Handle incoming Twilio call — create session via SessionRepository
    and start scripted intake.
    """
    try:
        logger.info(f"[TWILIO] Incoming call: {CallSid}")

        ensure_default_workflows_registered()
        route = get_workflow_route_resolver().resolve(called_phone_number=To)

        if route.safe_response_required:
            logger.error("[TWILIO] Incoming call could not be safely routed")
            twiml = await generate_twiml_say_and_hangup(
                "We are unable to safely route this call right now. "
                "If this is an emergency, please hang up and call 9 1 1. "
                "Otherwise, please contact the clinic directly."
            )
            return Response(content=twiml, media_type="application/xml")

        repo = get_session_repository()
        session = repo.create_session(call_sid=CallSid, workflow_route=route)
        session.channel_metadata["called_phone_number"] = To

        # Track scripted intake stage
        workflow = ensure_default_workflows_registered().get(route.workflow_id)
        intake = workflow.get_scripted_intake_definition()
        first_stage = (
            _initialize_scripted_intake(session, intake) if intake is not None else None
        )
        first_question = (
            _stage_prompt(first_stage)
            if first_stage is not None
            else "How can I help you today?"
        )

        # Generate greeting — split into non-interruptible preamble +
        # Gather for the name question.  This prevents barge-in from
        # cutting off the legal disclaimer and ensures Jenny is used
        # (long combined text was timing out TTS on cold start).
        greeting = intake.intro_text if intake and intake.intro_text else ""

        # Store greeting in conversation
        if greeting:
            session.conversation.append(
                ConversationTurn(role="assistant", text=greeting)
            )

        # Move to NAME stage
        session.channel_metadata.setdefault(
            "stage",
            first_stage.stage_id if first_stage else STAGE_DYNAMIC,
        )
        session.conversation.append(
            ConversationTurn(role="assistant", text=first_question)
        )

        repo.persist_session(session)

        if first_stage is not None:
            twiml = await _generate_initial_scripted_twiml(
                greeting,
                first_stage,
                "/api/v1/voice/gather",
            )
        else:
            twiml = await generate_twiml_gather(
                first_question,
                "/api/v1/voice/gather",
                timeout=8,
                speech_timeout="auto",
            )

        logger.info(f"[TWILIO] Session {session.session_id} started for call {CallSid}")
        return Response(content=twiml, media_type="application/xml")

    except Exception as e:
        logger.error(f"[TWILIO] Error in incoming call: {e}", exc_info=True)
        error_twiml = await generate_twiml_say_and_hangup(
            "Sorry, we encountered a technical error. Please try again later."
        )
        return Response(content=error_twiml, media_type="application/xml")


async def _recover_missing_twilio_session(
    *,
    repo,
    call_sid: str,
    called_phone_number: str | None,
) -> OrchestratorSession | None:
    """Create a safe continuation session when Twilio state lookup misses.

    This is a last-resort path for production drift: the caller has already
    answered a Gather, so hanging up loses care continuity. Recovery resumes in
    dynamic intake and lets the healthcare workflow ask for any missing SBAR
    details before finalization.
    """

    try:
        ensure_default_workflows_registered()
        route = get_workflow_route_resolver().resolve(
            called_phone_number=called_phone_number
        )
        if route.safe_response_required:
            logger.error(
                "[TWILIO] Missing-session recovery could not safely route call %s",
                call_sid,
            )
            return None

        session = repo.create_session(call_sid=call_sid, workflow_route=route)
        session.channel_metadata["called_phone_number"] = called_phone_number
        session.channel_metadata["stage"] = STAGE_DYNAMIC
        session.channel_metadata["session_recovery"] = {
            "reason": "missing_session_during_gather",
            "recovered_at": datetime.now(UTC).isoformat(),
        }
        repo.persist_session(session)
        logger.warning(
            "[TWILIO] Recovered missing session %s for call %s in dynamic intake",
            session.session_id,
            call_sid,
        )
        return session
    except Exception as exc:
        logger.error(
            "[TWILIO] Missing-session recovery failed for call %s: %s",
            call_sid,
            type(exc).__name__,
            exc_info=True,
        )
        return None


@router.post("/gather")
async def handle_gather(
    request: Request,
    background_tasks: BackgroundTasks,
    CallSid: str = Form(...),
    To: Optional[str] = Form(None),
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
            session = await _recover_missing_twilio_session(
                repo=repo,
                call_sid=CallSid,
                called_phone_number=To,
            )
            if session is None:
                twiml = await generate_twiml_say_and_hangup(
                    "Sorry, we encountered a technical error. Please try again later."
                )
                return Response(content=twiml, media_type="application/xml")

        if session.is_finalized:
            logger.warning(
                "[TWILIO] Gather received after finalized session %s for call %s",
                session.session_id,
                CallSid,
            )
            twiml = await generate_twiml_say_and_hangup(
                "This call has already been completed. "
                "If you need more help, please call back."
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

        intake = _get_scripted_intake_for_session(session)
        current_stage = session.channel_metadata.get("stage")
        if current_stage is None and intake and intake.stages:
            current_stage = intake.stages[0].stage_id
            session.channel_metadata["stage"] = current_stage
        logger.info(f"[TWILIO] Session {session_id} current stage: {current_stage}")

        scripted_stage = _current_scripted_stage(session, intake)
        if intake is not None and scripted_stage is not None:
            next_stage, reprompt = await _handle_scripted_stage_response(
                session,
                intake,
                scripted_stage,
                SpeechResult,
            )
            if reprompt:
                repo.persist_session(session)
                twiml = await generate_twiml_gather(
                    reprompt,
                    "/api/v1/voice/gather",
                    timeout=scripted_stage.timeout_seconds,
                    hints=scripted_stage.hints,
                    speech_timeout=scripted_stage.speech_timeout,
                )
                return Response(content=twiml, media_type="application/xml")
            if next_stage is not None:
                next_question = _stage_prompt(next_stage)
                session.conversation.append(
                    ConversationTurn(role="assistant", text=next_question)
                )
                repo.persist_session(session)
                twiml = await _prompt_for_scripted_stage(next_stage)
                return Response(content=twiml, media_type="application/xml")

            logger.info(f"[TWILIO] Session {session_id} entering DYNAMIC stage")
            repo.persist_session(session)
            current_stage = session.channel_metadata.get("stage", STAGE_DYNAMIC)

        # DYNAMIC stage — multi-agent orchestrator
        if session.channel_metadata.get("stage") == STAGE_DYNAMIC:
            # Kick off the orchestrator in the background and return
            # typing sounds immediately so the caller doesn't sit in silence.
            speech_text = SpeechResult.strip()

            async def _run_turn(sess, text):
                """Background coroutine: run workflow + persist."""
                import time as _t

                _t0 = _t.monotonic()
                context = _build_workflow_context(sess, request=request)
                workflow_input = WorkflowInput(
                    user_text=text,
                    session_state=sess.model_dump(mode="json"),
                    called_phone_number=sess.channel_metadata.get(
                        "called_phone_number"
                    ),
                    metadata={"channel": "twilio"},
                )
                turn_result = await get_workflow_engine().handle_turn(
                    context,
                    workflow_input,
                )
                updated_session = OrchestratorSession.model_validate(
                    turn_result.updated_state
                )
                res = _workflow_turn_to_legacy_result(turn_result)
                _ms = (_t.monotonic() - _t0) * 1000
                logger.info(
                    f"[TWILIO] Workflow completed in {_ms:.0f}ms for session {sess.session_id}"
                )
                r = get_session_repository()
                r.persist_session(updated_session)
                return res, updated_session

            task = asyncio.create_task(_run_turn(session, speech_text))
            _pending_turns[CallSid] = (task, session)
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
        pending = _pending_turns.get(CallSid)

        # No pending task — shouldn't happen, but recover gracefully
        if pending is None:
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

        task, session_obj = pending

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

        task_result = task.result()
        if (
            isinstance(task_result, tuple)
            and len(task_result) == 2
            and isinstance(task_result[1], OrchestratorSession)
        ):
            result, session_obj = task_result
        else:
            result = task_result
        action = result["action"]
        spoken_message = result["message"]

        # Use the in-memory session object from the completed task.
        # Do NOT reload from DB — persist_session() already changed the
        # status to "escalated"/"ended", so load_session_by_call()
        # (which filters by status=="active") would return None and
        # silently skip report generation.
        session = session_obj
        session_id = session.session_id if session else CallSid

        import time as _time

        _t_tts = _time.monotonic()
        repo = get_session_repository()

        if action == "escalate":
            logger.info(f"[TWILIO] Deterministic escalation for session {session_id}")
            if session:
                session.is_finalized = True
                _persist_finalization_reason_from_result(
                    session,
                    result,
                    default_reason="workflow_error",
                )
                repo.persist_session(session)
                if _is_healthcare_session(session):
                    background_tasks.add_task(
                        _generate_orchestrator_report_background,
                        session_id=session_id,
                        orch_session=session,
                        session_metadata=_build_session_metadata(session),
                    )
                else:
                    background_tasks.add_task(
                        _generate_platform_report_background,
                        session_id=session_id,
                        session=session,
                    )
            twiml = await generate_twiml_say_and_hangup(spoken_message)
            return Response(content=twiml, media_type="application/xml")

        elif action == "finalize":
            logger.info(f"[TWILIO] Finalizing session {session_id}")
            if session:
                session.is_finalized = True
                _persist_finalization_reason_from_result(
                    session,
                    result,
                    default_reason="sufficient_information",
                )
                repo.persist_session(session)
                if _is_healthcare_session(session):
                    background_tasks.add_task(
                        _generate_orchestrator_report_background,
                        session_id=session_id,
                        orch_session=session,
                        session_metadata=_build_session_metadata(session),
                    )
                else:
                    background_tasks.add_task(
                        _generate_platform_report_background,
                        session_id=session_id,
                        session=session,
                    )
            if _is_healthcare_session(session):
                twiml = await generate_twiml_handoff(spoken_message)
            else:
                twiml = await generate_twiml_say_and_hangup(spoken_message)
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


def _is_healthcare_session(session: OrchestratorSession | None) -> bool:
    if session is None:
        return True
    try:
        from src.verticals.healthcare.constants import HEALTHCARE_TRIAGE_WORKFLOW_ID

        return (session.workflow_id or HEALTHCARE_TRIAGE_WORKFLOW_ID) == (
            HEALTHCARE_TRIAGE_WORKFLOW_ID
        )
    except Exception:
        return (session.workflow_id or "healthcare_triage_v1") == "healthcare_triage_v1"


async def _generate_platform_report_background(
    session_id: str,
    session: OrchestratorSession,
) -> None:
    """Run generic post-call extraction for non-healthcare workflows."""

    try:
        repo = get_session_repository()
        context = _build_workflow_context(session)
        final_result = _workflow_final_result_from_session(context, session)
        session.channel_metadata["workflow_final_result"] = final_result.model_dump(
            mode="json"
        )
        repo.persist_session(session)
        _run_post_call_extraction(session, final_result=final_result)
        logger.info(
            "[BACKGROUND] Generic workflow report complete for session %s",
            session_id,
        )
    except Exception as exc:
        logger.warning(
            "[BACKGROUND] Generic workflow report failed for session %s: %s",
            session_id,
            type(exc).__name__,
            exc_info=True,
        )


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


def _build_workflow_context(
    session: OrchestratorSession,
    request: Request | None = None,
) -> WorkflowContext:
    """Build a generic workflow context from a stored orchestrator session."""
    request_id = request.headers.get("X-Request-ID") if request else None
    route_metadata = session.channel_metadata.get("route", {})
    return WorkflowContext(
        session_id=session.session_id,
        organization_id=session.organization_id
        or route_metadata.get("organization_id"),
        phone_number_id=session.phone_number_id
        or route_metadata.get("phone_number_id"),
        call_sid=session.call_sid,
        vertical=session.vertical_key or "healthcare",
        workflow_id=session.workflow_id or "healthcare_triage_v1",
        workflow_version=session.workflow_version or "v1",
        metadata={
            "channel": "twilio",
            "stage": session.channel_metadata.get("stage"),
            "route": route_metadata,
        },
        request_id=request_id,
        correlation_id=request_id,
    )


def _workflow_turn_to_legacy_result(turn_result: WorkflowTurnResult) -> dict:
    """Convert platform workflow result to the legacy Twilio action contract."""
    if turn_result.escalation_required:
        action = "escalate"
    elif turn_result.should_finalize:
        action = "finalize"
    else:
        action = "ask"

    return {
        "action": action,
        "message": turn_result.assistant_text,
        "workflow_turn": turn_result.model_dump(mode="json"),
    }


def _persist_finalization_reason_from_result(
    session: OrchestratorSession,
    result: dict,
    default_reason: str,
) -> None:
    """Keep finalization reason persisted when Twilio closes a workflow turn."""

    if session.finalization_reason:
        session.channel_metadata["finalization_reason"] = session.finalization_reason
        return

    workflow_turn = result.get("workflow_turn") or {}
    audit_metadata = workflow_turn.get("audit_metadata") or {}
    reason = (
        result.get("finalization_reason")
        or audit_metadata.get("finalization_reason")
        or _normalise_finalization_fail_reason(result.get("fail_reason"))
        or default_reason
    )
    session.finalization_reason = reason
    session.channel_metadata["finalization_reason"] = reason


def _normalise_finalization_fail_reason(fail_reason: object) -> str | None:
    if not isinstance(fail_reason, str) or not fail_reason:
        return None
    if fail_reason.startswith("confused_caller"):
        return "repeated_unclear_answers"
    if fail_reason.startswith("llm_timeout"):
        return "llm_timeout"
    if "validation" in fail_reason or "json" in fail_reason or "schema" in fail_reason:
        return "llm_validation_failure"
    if fail_reason.startswith("post_check_violation"):
        return "post_check_safety_failure"
    if fail_reason.startswith("low_confidence_with_red_flags"):
        return "red_flag_score_threshold"
    if fail_reason.startswith("red_flag_exception"):
        return "workflow_error"
    return None


def _workflow_final_result_from_session(
    context: WorkflowContext,
    session: OrchestratorSession,
) -> WorkflowFinalResult:
    metadata_result = session.channel_metadata.get("workflow_final_result")
    if metadata_result:
        return WorkflowFinalResult.model_validate(metadata_result)

    registry = ensure_default_workflows_registered()
    workflow = registry.get(context.workflow_id)
    if hasattr(workflow, "build_final_result_from_session"):
        return workflow.build_final_result_from_session(context, session)
    raise RuntimeError("Synchronous workflow final result builder is unavailable")


def _run_post_call_extraction(
    session: OrchestratorSession,
    final_result: WorkflowFinalResult | None = None,
) -> None:
    """Run read-only post-call extraction for workflows that support it."""
    from src.platform.extraction.service import get_extraction_service
    from src.platform.workflows.registry import ensure_default_workflows_registered

    if not session.is_finalized:
        logger.warning(
            "[BACKGROUND] Skipping post-call extraction before finalized result "
            "is stored for session %s",
            session.session_id,
        )
        return

    context = _build_workflow_context(session)
    registry = ensure_default_workflows_registered()
    workflow = registry.get(context.workflow_id)
    definition = workflow.get_definition()
    if not definition.supports_post_call_extraction:
        return

    if final_result is None:
        final_result = _workflow_final_result_from_session(context, session)

    transcript = [turn.model_dump(mode="json") for turn in session.conversation]
    get_extraction_service().extract_and_persist(
        transcript=transcript,
        final_result=final_result,
        workflow_context=context,
        extraction_schema=workflow.get_extraction_schema(),
    )
