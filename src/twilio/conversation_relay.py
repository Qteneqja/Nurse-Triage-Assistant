"""
Twilio ConversationRelay transport (streaming WebSocket) — SKELETON.

This is the additive streaming voice transport that runs ALONGSIDE the legacy
``<Gather>``/TwiML path in ``routes.py``. It is selected by
``VOICE_PIPELINE=conversation_relay`` and feeds the EXISTING, unchanged
orchestrator / workflow-engine entry points — it contains ZERO triage or safety
logic of its own (see ADR-0002 and the voice-pipeline-operations skill).

Audit basis (Step 0): the orchestrator/engine accept plain caller text + a
session and return plain text; ``src/safety`` and ``src/orchestrator`` are
transport-agnostic, so this swap is purely additive transport glue.

SKELETON SCOPE (checkpoint 1): WebSocket lifecycle + provider seam + the
simplest end-to-end turn through the unchanged workflow engine:
  setup -> greeting; prompt(last) -> one dynamic turn -> response; finalize /
  escalate -> out-of-band report + ``end``.

DEFERRED to checkpoint 2 (re-host the remaining transport glue from routes.py):
  the scripted-intake state machine, the shared-number vertical menu, DTMF /
  Birchwood "0" transfer, full idempotency / WS reconnect-resume, richer
  fail-closed handling, barge-in/interrupt semantics, and the nurse warm-transfer
  (``<Dial>`` -> CR handoff). Until then, a CR call goes straight to the dynamic
  orchestrator turn.

Provider seam (``VOICE_OUTPUT_MODE``):
  "azure_play" (default, no voice regression) — render with the existing Azure
  TTS pipeline and send the MP3 URL as a CR ``play`` message.
  "cr_native" — stream assistant text as CR ``text`` tokens; Twilio's CR TTS
  renders it (changes the voice identity).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from xml.sax import saxutils

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import src.config as config
from src.orchestrator.schemas import ConversationTurn, OrchestratorSession
from src.platform.workflows.registry import ensure_default_workflows_registered
from src.platform.workflows.router import (
    get_workflow_engine,
    get_workflow_route_resolver,
)
from src.platform.workflows.schemas import WorkflowInput
from src.safety.injury_detection import INJURY_SAFETY_ADVISORY
from src.storage.session_repository import get_session_repository
from src.twilio import webhook_stability as stability

logger = logging.getLogger(__name__)

# Separate router: NO Twilio-signature HTTP dependency (a WebSocket upgrade does
# not carry the X-Twilio-Signature header). Auth is via the ?token= query param.
ws_router = APIRouter()


# ---------------------------------------------------------------------------
# TwiML entry: <Connect><ConversationRelay .../></Connect>
# ---------------------------------------------------------------------------


def derive_wss_url() -> str:
    """Return the wss:// URL Twilio should open, or '' if it can't be derived."""
    if config.CONVERSATION_RELAY_WSS_URL:
        url = config.CONVERSATION_RELAY_WSS_URL
    else:
        base = config.TWILIO_WEBHOOK_BASE_URL.strip()
        if not base:
            return ""
        scheme_stripped = base.split("://", 1)[-1].rstrip("/")
        url = f"wss://{scheme_stripped}/api/v1/voice/relay"
    if config.CONVERSATION_RELAY_WS_TOKEN:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}token={config.CONVERSATION_RELAY_WS_TOKEN}"
    return url


def build_conversation_relay_twiml() -> str:
    """Build the <Connect><ConversationRelay> TwiML returned by /incoming.

    Returns '' when the wss URL cannot be derived, so the caller can fall back
    to the legacy gather path rather than starting a broken call.
    """
    url = derive_wss_url()
    if not url:
        return ""

    attrs = [f"url={saxutils.quoteattr(url)}"]
    # STT (transcription) provider/model.
    if config.CR_TRANSCRIPTION_PROVIDER:
        attrs.append(
            f"transcriptionProvider={saxutils.quoteattr(config.CR_TRANSCRIPTION_PROVIDER)}"
        )
    if config.CR_SPEECH_MODEL:
        attrs.append(f"speechModel={saxutils.quoteattr(config.CR_SPEECH_MODEL)}")
    # TTS provider/voice only matter when CR renders speech (cr_native or the
    # welcome greeting). We send our own greeting on setup, so omit welcomeGreeting.
    if config.VOICE_OUTPUT_MODE == "cr_native":
        if config.CR_TTS_PROVIDER:
            attrs.append(f"ttsProvider={saxutils.quoteattr(config.CR_TTS_PROVIDER)}")
        if config.CR_TTS_VOICE:
            attrs.append(f"voice={saxutils.quoteattr(config.CR_TTS_VOICE)}")
    attrs.append('dtmfDetection="true"')
    attrs.append('interruptible="any"')

    attr_str = " ".join(attrs)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f"    <ConversationRelay {attr_str}/>\n"
        "  </Connect>\n"
        "</Response>"
    )


# ---------------------------------------------------------------------------
# Out-of-band task scheduler (replaces FastAPI BackgroundTasks on the WS path)
# ---------------------------------------------------------------------------


class _AsyncTaskScheduler:
    """BackgroundTasks-compatible shim that runs work off the WS turn.

    The legacy HTTP path uses FastAPI's BackgroundTasks; on a persistent
    WebSocket we schedule the same callables with asyncio so the deferred
    finalize/report contract is preserved (finalize() runs OUT-OF-BAND, never
    inline on the turn path).
    """

    def add_task(self, func, *args, **kwargs) -> None:
        if inspect.iscoroutinefunction(func):
            asyncio.create_task(func(*args, **kwargs))
        else:
            asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))


# ---------------------------------------------------------------------------
# Relay session handler
# ---------------------------------------------------------------------------


class ConversationRelaySession:
    """One ConversationRelay WebSocket connection ≈ one phone call."""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self.call_sid: str | None = None
        self.session_id: str | None = None
        self.closed = False
        self._turn_lock = asyncio.Lock()

    # -- dispatch -----------------------------------------------------------

    async def dispatch(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "setup":
            await self._handle_setup(msg)
        elif msg_type == "prompt":
            await self._handle_prompt(msg)
        elif msg_type == "interrupt":
            # Barge-in: CR already stopped playback. Full re-host of interrupt
            # semantics is checkpoint 2.
            logger.info("[CR] interrupt (call_sid=%s)", self.call_sid)
        elif msg_type == "dtmf":
            # DTMF menu/transfer routing is checkpoint 2.
            logger.info(
                "[CR] dtmf digit=%s (call_sid=%s)", msg.get("digit"), self.call_sid
            )
        elif msg_type == "error":
            logger.error(
                "[CR] error from Twilio: %s (call_sid=%s)",
                msg.get("description"),
                self.call_sid,
            )
        else:
            logger.warning("[CR] unknown message type=%s", msg_type)

    # -- setup --------------------------------------------------------------

    async def _handle_setup(self, msg: dict) -> None:
        self.call_sid = msg.get("callSid") or msg.get("CallSid")
        called = msg.get("to")
        if not self.call_sid:
            logger.error("[CR] setup without callSid — closing")
            await self._end()
            return

        ensure_default_workflows_registered()
        repo = get_session_repository()

        existing = repo.load_session_by_call(self.call_sid)
        if existing is not None and not existing.is_finalized:
            session = existing
            logger.info(
                "[CR] reconnect/duplicate setup for active call %s (session %s)",
                self.call_sid,
                session.session_id,
            )
        else:
            route = get_workflow_route_resolver().resolve(called_phone_number=called)
            if route.safe_response_required:
                logger.error("[CR] call could not be safely routed — ending")
                await self._send_text(
                    "We are unable to safely route this call right now. If this is "
                    "an emergency, please hang up and call 9 1 1."
                )
                await self._end()
                return
            session = repo.create_session(call_sid=self.call_sid, workflow_route=route)
            session.channel_metadata["called_phone_number"] = called
            session.channel_metadata["channel"] = "conversationrelay"
            # SKELETON: go straight to the dynamic orchestrator turn. The
            # scripted-intake state machine is re-hosted in checkpoint 2.
            session.channel_metadata["stage"] = "DYNAMIC"

        self.session_id = session.session_id

        greeting = self._greeting_for(session)
        if greeting:
            session.conversation.append(
                ConversationTurn(role="assistant", text=greeting)
            )
        repo.persist_session(session)
        if greeting:
            await self._send_response(greeting, session)
        logger.info(
            "[CR] session %s ready for call %s", session.session_id, self.call_sid
        )

    def _greeting_for(self, session: OrchestratorSession) -> str:
        try:
            workflow = ensure_default_workflows_registered().get(session.workflow_id)
            intake = workflow.get_scripted_intake_definition()
            if intake is not None and getattr(intake, "intro_text", ""):
                return intake.intro_text
        except Exception:
            logger.debug("[CR] no scripted intro available", exc_info=True)
        return "Hello, thank you for calling. How can I help you today?"

    # -- prompt (one caller turn) ------------------------------------------

    async def _handle_prompt(self, msg: dict) -> None:
        # Only act on a completed utterance; partials (last=False) are ignored
        # in the skeleton.
        if not msg.get("last", False):
            return
        speech = (msg.get("voicePrompt") or "").strip()
        if not speech or self.call_sid is None:
            return

        # Serialize turns: never run two orchestrator turns for one call at once.
        async with self._turn_lock:
            repo = get_session_repository()
            session = repo.load_session_by_call(self.call_sid)
            if session is None or session.is_finalized:
                logger.warning(
                    "[CR] prompt for missing/finalized session (call %s)", self.call_sid
                )
                return

            result, session = await self._run_turn(session, speech)
            action = result["action"]
            spoken = result["message"]

            # Injury advisory (Invariant 3): prepend once if the final result
            # flagged injuries and it was never spoken. Healthcare never enters
            # this branch. Mirrors the /thinking handler.
            from src.twilio.routes import (
                _is_healthcare_session,
                _workflow_result_flags,
            )

            if (
                not _is_healthcare_session(session)
                and not stability.injury_advisory_already_given(session)
                and "injuries_reported" in _workflow_result_flags(session)
            ):
                spoken = f"{INJURY_SAFETY_ADVISORY} {spoken}"
                stability.mark_injury_advisory_given(session)

            if action in ("finalize", "escalate"):
                await self._finalize(session, result, action, spoken)
            else:
                await self._send_response(spoken, session)

    async def _run_turn(self, session: OrchestratorSession, text: str):
        """Run one workflow-engine turn — the SAME path the gather route uses."""
        # Imported lazily to avoid any import-order coupling with routes.py.
        from src.twilio.routes import (
            _build_workflow_context,
            _workflow_turn_to_legacy_result,
        )

        context = _build_workflow_context(session)
        workflow_input = WorkflowInput(
            user_text=text,
            session_state=session.model_dump(mode="json"),
            called_phone_number=session.channel_metadata.get("called_phone_number"),
            metadata={"channel": "conversationrelay"},
        )
        turn_result = await get_workflow_engine().handle_turn(context, workflow_input)
        updated = OrchestratorSession.model_validate(turn_result.updated_state)
        get_session_repository().persist_session(updated)
        return _workflow_turn_to_legacy_result(turn_result), updated

    async def _finalize(
        self,
        session: OrchestratorSession,
        result: dict,
        action: str,
        spoken: str,
    ) -> None:
        """Preserve the deferred-finalize contract: speak, then dispatch the
        real finalize()/report OUT-OF-BAND, then end the call."""
        from src.twilio.routes import (
            _build_session_metadata,
            _generate_orchestrator_report_background,
            _generate_platform_report_background,
            _is_healthcare_session,
            _maybe_schedule_enrichment,
            _persist_finalization_reason_from_result,
        )

        repo = get_session_repository()
        session.is_finalized = True
        _persist_finalization_reason_from_result(
            session,
            result,
            default_reason=(
                "workflow_error" if action == "escalate" else "sufficient_information"
            ),
        )
        repo.persist_session(session)

        scheduler = _AsyncTaskScheduler()
        if _is_healthcare_session(session):
            scheduler.add_task(
                _generate_orchestrator_report_background,
                session_id=session.session_id,
                orch_session=session,
                session_metadata=_build_session_metadata(session),
            )
        else:
            scheduler.add_task(
                _generate_platform_report_background,
                session_id=session.session_id,
                session=session,
            )
        _maybe_schedule_enrichment(scheduler, session)

        await self._send_response(spoken, session)
        # NOTE: nurse warm-transfer (<Dial> -> CR handoff) is checkpoint 2; the
        # skeleton ends the session after the closing message.
        await self._end()

    # -- output provider seam ----------------------------------------------

    async def _send_response(
        self, text: str, session: OrchestratorSession | None
    ) -> None:
        if not text:
            return
        if config.VOICE_OUTPUT_MODE == "cr_native":
            await self._send_text(text)
            return
        # azure_play (default): keep the exact Azure voice via a play URL.
        from src.twilio.routes import _tts_audio_url

        try:
            url = await _tts_audio_url(text, session)
        except Exception:
            logger.warning(
                "[CR] Azure TTS failed; falling back to CR text", exc_info=True
            )
            url = None
        if url:
            await self._send(json.dumps({"type": "play", "source": url}))
        else:
            await self._send_text(text)

    async def _send_text(self, text: str) -> None:
        await self._send(json.dumps({"type": "text", "token": text, "last": True}))

    async def _end(self) -> None:
        await self._send(json.dumps({"type": "end"}))
        self.closed = True

    async def _send(self, payload: str) -> None:
        try:
            await self._ws.send_text(payload)
        except Exception:
            logger.warning("[CR] failed to send WS message", exc_info=True)
            self.closed = True

    async def aclose(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@ws_router.websocket("/relay")
async def conversation_relay_ws(websocket: WebSocket) -> None:
    """ConversationRelay WebSocket endpoint (mounted at /api/v1/voice/relay)."""
    token = websocket.query_params.get("token")
    if (
        config.CONVERSATION_RELAY_WS_TOKEN
        and token != config.CONVERSATION_RELAY_WS_TOKEN
    ):
        logger.warning("[CR] rejected WS connection: bad/missing token")
        await websocket.close(code=1008)
        return

    await websocket.accept()
    handler = ConversationRelaySession(websocket)
    try:
        while not handler.closed:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[CR] non-JSON WS frame ignored")
                continue
            await handler.dispatch(msg)
    except WebSocketDisconnect:
        logger.info("[CR] WebSocket disconnected (call_sid=%s)", handler.call_sid)
    except Exception as exc:
        logger.error("[CR] relay loop error: %s", exc, exc_info=True)
    finally:
        await handler.aclose()
