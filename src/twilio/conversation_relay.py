"""
Twilio ConversationRelay transport (streaming WebSocket).

The additive streaming voice transport that runs ALONGSIDE the legacy
``<Gather>``/TwiML path in ``routes.py``. Selected by
``VOICE_PIPELINE=conversation_relay``. It feeds the EXISTING, unchanged
orchestrator / workflow-engine entry points and reuses the SAME
transport-neutral helpers the gather path uses (scripted-intake state machine,
narrative capture, injury advisory, finalize/report dispatch) — it contains ZERO
triage or safety logic of its own (see ADR-0002 + the voice-pipeline-operations
skill). The gather path is left untouched, so the live pilot cannot regress.

Audit basis (Step 0): the orchestrator/engine accept plain caller text + a
session and return plain text; ``src/safety`` and ``src/orchestrator`` are
transport-agnostic. Output is turn-batched (one complete gated string per turn),
so token streaming is purely a transport concern — the orchestrator is NOT a
generator and the safety gate still runs on the complete text.

Parity covered: scripted intake (incl. multi-segment narrative + injury advisory
+ dynamic/readback prompts), the dynamic orchestrator turn, finalize/escalate
with OUT-OF-BAND report dispatch (deferred-finalize contract preserved), nurse
warm-transfer on healthcare finalize (CR ``end`` -> ``<Connect action>`` ->
``<Dial>``), Birchwood "0"/spoken transfer, DTMF + barge-in/interrupt handling,
and WS reconnect/duplicate-setup reuse.

DEFERRED (documented): the shared-number vertical menu (a non-pilot config that
needs a WS menu rendering) — a CR call to a shared-menu number proceeds straight
to its resolved workflow's scripted intake. The legacy gather path still serves
shared-menu numbers.

Provider seam (``VOICE_OUTPUT_MODE``): "azure_play" (default, no voice
regression) renders via the existing Azure TTS pipeline and sends the MP3 URL as
a CR ``play`` message; "cr_native" streams ``text`` tokens for Twilio's CR TTS.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from xml.sax import saxutils

from fastapi import APIRouter, Depends, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

import src.config as config
from src.orchestrator.schemas import ConversationTurn, OrchestratorSession
from src.platform.workflows.registry import ensure_default_workflows_registered
from src.platform.workflows.router import (
    get_workflow_engine,
    get_workflow_route_resolver,
)
from src.platform.workflows.schemas import WorkflowInput
from src.safety.injury_detection import INJURY_SAFETY_ADVISORY
from src.security.twilio_signature import validate_twilio_signature
from src.storage.session_repository import get_session_repository
from src.twilio import routes as rt
from src.twilio import webhook_stability as stability

logger = logging.getLogger(__name__)

# WebSocket router: NO Twilio-signature HTTP dependency (a WS upgrade carries no
# X-Twilio-Signature header). Auth is via the ?token= query param.
ws_router = APIRouter()

# HTTP router for the <Connect action> handoff callback — signature-validated
# like every other Twilio webhook.
http_router = APIRouter(dependencies=[Depends(validate_twilio_signature)])


# ---------------------------------------------------------------------------
# URL derivation + TwiML entry
# ---------------------------------------------------------------------------


def _base_host() -> str:
    if config.CONVERSATION_RELAY_WSS_URL:
        return config.CONVERSATION_RELAY_WSS_URL.split("://", 1)[-1].rsplit("/api/", 1)[
            0
        ]
    base = config.TWILIO_WEBHOOK_BASE_URL.strip()
    if not base:
        return ""
    return base.split("://", 1)[-1].rstrip("/")


def derive_wss_url() -> str:
    """Return the wss:// URL Twilio should open, or '' if it can't be derived."""
    if config.CONVERSATION_RELAY_WSS_URL:
        url = config.CONVERSATION_RELAY_WSS_URL
    else:
        host = _base_host()
        if not host:
            return ""
        url = f"wss://{host}/api/v1/voice/relay"
    if config.CONVERSATION_RELAY_WS_TOKEN:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}token={config.CONVERSATION_RELAY_WS_TOKEN}"
    return url


def _derive_action_url() -> str:
    """HTTPS URL Twilio calls when the ConversationRelay <Connect> ends."""
    host = _base_host()
    return f"https://{host}/api/v1/voice/relay-action" if host else ""


def build_conversation_relay_twiml() -> str:
    """Build the <Connect><ConversationRelay> TwiML returned by /incoming.

    Returns '' when the wss URL cannot be derived, so the caller can fall back
    to the legacy gather path rather than starting a broken call.
    """
    url = derive_wss_url()
    if not url:
        return ""

    connect_attrs = ""
    action_url = _derive_action_url()
    if action_url:
        connect_attrs = f" action={saxutils.quoteattr(action_url)}"

    attrs = [f"url={saxutils.quoteattr(url)}"]
    if config.CR_TRANSCRIPTION_PROVIDER:
        attrs.append(
            f"transcriptionProvider={saxutils.quoteattr(config.CR_TRANSCRIPTION_PROVIDER)}"
        )
    if config.CR_SPEECH_MODEL:
        attrs.append(f"speechModel={saxutils.quoteattr(config.CR_SPEECH_MODEL)}")
    # TTS provider/voice only matter when CR renders speech (cr_native). We send
    # our own greeting on setup, so omit welcomeGreeting.
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
        f"  <Connect{connect_attrs}>\n"
        f"    <ConversationRelay {attr_str}/>\n"
        "  </Connect>\n"
        "</Response>"
    )


# ---------------------------------------------------------------------------
# <Connect action> handoff callback: Dial the nurse or hang up.
# ---------------------------------------------------------------------------


@http_router.post("/relay-action")
async def conversation_relay_action(
    request: Request,
    CallSid: str = Form(...),
    HandoffData: str | None = Form(None),
):
    """Called by Twilio when the ConversationRelay <Connect> ends.

    Reproduces generate_twiml_handoff: <Dial> the nurse queue when the WS handler
    requested it via the `end` message's handoffData (healthcare finalize +
    NURSE_TRANSFER_NUMBER), otherwise <Hangup>.

    The decision is carried in handoffData (set server-side at finalize time)
    rather than reloaded from storage, because a finalized session is no longer
    returned by load_session_by_call. The dial NUMBER is never trusted from the
    payload — only the dial/hangup intent — and the number comes from config.
    NOTE: the exact param name Twilio uses ("HandoffData") should be confirmed in
    staging; an unrecognized/absent payload safely falls back to <Hangup>.
    """
    action = None
    raw = HandoffData
    if raw is None:
        form = await request.form()
        raw = form.get("HandoffData") or form.get("handoffData")
    if raw:
        try:
            action = (json.loads(raw) or {}).get("action")
        except (json.JSONDecodeError, TypeError):
            action = None

    if action == "dial" and config.NURSE_TRANSFER_NUMBER:
        num = saxutils.escape(config.NURSE_TRANSFER_NUMBER)
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response>\n    <Dial>{num}</Dial>\n</Response>"
        )
    elif action == "dial_birchwood" and config.BIRCHWOOD_TRANSFER_NUMBER:
        # Birchwood warm transfer (parity with the gather path): the number
        # comes from config ONLY — handoffData carries the intent, never a
        # number. The <Dial action> callback (/birchwood-dial-status,
        # signature-validated) speaks the honest callback close if the dial
        # is busy/unanswered/fails, so the caller is never stranded.
        num = saxutils.escape(config.BIRCHWOOD_TRANSFER_NUMBER)
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'    <Dial action="/api/v1/voice/birchwood-dial-status" '
            f'method="POST" timeout="20">{num}</Dial>\n'
            "</Response>"
        )
    else:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n    <Hangup/>\n</Response>"
        )
    return Response(content=twiml, media_type="application/xml")


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


# Short, pre-approved acknowledgments for the instant-ack perceived-latency option
# (CR_INSTANT_ACK). Deterministic and safe — no LLM — so they can be spoken the
# moment the caller finishes, while the gated turn computes behind them.
_INSTANT_ACKS = ("Okay.", "Alright.", "Got it.", "Sure thing.", "One moment.")


def _estimate_settle_seconds(text: str) -> float:
    """Seconds to let ConversationRelay finish speaking ``text`` before we end.

    CR ends the session the instant it receives ``end`` and cuts off any audio
    still playing, so a finalize/transfer/fail-closed goodbye drops mid-sentence.
    We hold for an estimate of the TTS duration first: ~13 characters/second of
    spoken English (a deliberately conservative under-estimate of the rate, so we
    never cut the message short), floored so even a short line plays and bounded
    by ``CR_FINAL_SPEECH_SETTLE_MAX_S`` (0 disables it — e.g. in tests).
    """
    cap = config.CR_FINAL_SPEECH_SETTLE_MAX_S
    if cap <= 0 or not text:
        return 0.0
    return min(max(len(text) / 13.0, 1.0), cap) + 0.4


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
        elif msg_type == "dtmf":
            await self._handle_dtmf(msg)
        elif msg_type == "interrupt":
            # Barge-in: CR already stopped playback (interruptible="any"). We log
            # how much was spoken; trimming the stored transcript to the heard
            # portion is a refinement left as a TODO.
            logger.info(
                "[CR] interrupt after %sms (call_sid=%s)",
                msg.get("durationUntilInterruptMs"),
                self.call_sid,
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
            # WS reconnect / duplicate setup — resume the existing session and
            # re-prompt the current stage rather than creating a second session.
            self.session_id = existing.session_id
            logger.info(
                "[CR] reconnect/duplicate setup for active call %s (session %s)",
                self.call_sid,
                existing.session_id,
            )
            await self._reprompt_current(existing)
            return

        route = get_workflow_route_resolver().resolve(called_phone_number=called)
        if route.safe_response_required:
            logger.error("[CR] call could not be safely routed — ending")
            await self._send_response(
                "We are unable to safely route this call right now. If this is "
                "an emergency, please hang up and call 9 1 1.",
                None,
                interruptible=False,
            )
            await self._end()
            return

        session = repo.create_session(call_sid=self.call_sid, workflow_route=route)
        session.channel_metadata["called_phone_number"] = called
        session.channel_metadata["channel"] = "conversationrelay"
        self.session_id = session.session_id

        intake = rt._get_scripted_intake_for_session(session)
        first_stage = (
            rt._initialize_scripted_intake(session, intake)
            if intake is not None
            else None
        )
        greeting = intake.intro_text if intake and intake.intro_text else ""
        if greeting:
            session.conversation.append(
                ConversationTurn(role="assistant", text=greeting)
            )

        if first_stage is None:
            # No scripted intake — go straight to the dynamic orchestrator turn.
            session.channel_metadata.setdefault("stage", rt.STAGE_DYNAMIC)
            repo.persist_session(session)
            await self._send_response(
                greeting or "Hello, thank you for calling. How can I help you today?",
                session,
                interruptible=False,
            )
            return

        repo.persist_session(session)
        # Greeting/disclaimer first (non-interruptible), then the first question.
        if greeting:
            await self._send_response(greeting, session, interruptible=False)
        await self._send_stage_prompt(session, first_stage)
        logger.info(
            "[CR] session %s ready for call %s", session.session_id, self.call_sid
        )

    async def _reprompt_current(self, session: OrchestratorSession) -> None:
        intake = rt._get_scripted_intake_for_session(session)
        stage = rt._current_scripted_stage(session, intake)
        if stage is not None:
            await self._send_stage_prompt(session, stage)
        else:
            await self._send_response(
                "Sorry, let's continue. Could you repeat your last answer?",
                session,
            )

    # -- prompt (one caller turn) ------------------------------------------

    async def _handle_prompt(self, msg: dict) -> None:
        if not msg.get("last", False):
            return  # ignore partial transcripts
        speech = (msg.get("voicePrompt") or "").strip()
        if not speech or self.call_sid is None:
            return

        async with self._turn_lock:
            repo = get_session_repository()
            session = repo.load_session_by_call(self.call_sid)
            if session is None or session.is_finalized:
                logger.warning(
                    "[CR] prompt for missing/finalized session (call %s)",
                    self.call_sid,
                )
                return
            try:
                await self._advance(session, repo, speech, digits=None)
            except Exception:
                logger.error("[CR] turn failed — failing closed", exc_info=True)
                await self._fail_closed(session, repo, reason="cr_turn_error")

    async def _handle_dtmf(self, msg: dict) -> None:
        digit = msg.get("digit")
        if self.call_sid is None:
            return
        async with self._turn_lock:
            repo = get_session_repository()
            session = repo.load_session_by_call(self.call_sid)
            if session is None or session.is_finalized:
                return
            if rt._is_birchwood_scripted_transfer_request(session, None, digit):
                await self._run_transfer(session, repo, digit or "0")
                return
            logger.info("[CR] dtmf digit=%s (no binding) call=%s", digit, self.call_sid)

    async def _advance(
        self,
        session: OrchestratorSession,
        repo,
        speech: str,
        digits: str | None,
    ) -> None:
        """Mirror handle_gather's per-turn sequencing over the WebSocket."""
        # Birchwood "0"/spoken transfer.
        if rt._is_birchwood_scripted_transfer_request(session, speech, digits):
            await self._run_transfer(session, repo, digits if digits == "0" else speech)
            return

        intake = rt._get_scripted_intake_for_session(session)
        if session.channel_metadata.get("stage") is None and intake and intake.stages:
            session.channel_metadata["stage"] = intake.stages[0].stage_id
        scripted_stage = rt._current_scripted_stage(session, intake)

        if intake is not None and scripted_stage is not None:
            injury_advisory = stability.injury_advisory_if_needed(session, speech)

            # Multi-segment narrative capture: keep listening until the caller
            # signals they're done (or the hard cap is hit).
            if stability.stage_is_multi_segment(scripted_stage):
                session.conversation.append(
                    ConversationTurn(role="caller", text=speech)
                )
                ingest = stability.narrative_ingest(session, scripted_stage, speech)
                if not ingest.completed:
                    repo.persist_session(session)
                    await self._send_response(
                        stability.join_preamble(
                            injury_advisory, ingest.continuation_prompt
                        ),
                        session,
                    )
                    return
                speech = ingest.joined_text or speech
                rt._apply_narrative_prefill(session, scripted_stage, speech)
            else:
                session.conversation.append(
                    ConversationTurn(role="caller", text=speech)
                )

            next_stage, reprompt = await rt._handle_scripted_stage_response(
                session, intake, scripted_stage, speech
            )
            if injury_advisory is None:
                injury_advisory = stability.injury_advisory_for_recorded_state(session)

            if reprompt:
                repo.persist_session(session)
                await self._send_response(
                    stability.join_preamble(injury_advisory, reprompt), session
                )
                return

            if next_stage is not None:
                repo.persist_session(session)
                preamble = stability.join_preamble(
                    injury_advisory,
                    rt._scripted_stage_acknowledgement(scripted_stage, session),
                )
                await self._send_stage_prompt(session, next_stage, preamble=preamble)
                return

            # Scripted intake complete → DYNAMIC.
            repo.persist_session(session)
            if (
                rt._is_healthcare_session(session)
                and scripted_stage.stage_id != rt.STAGE_CHIEF_COMPLAINT
            ):
                first_dynamic_question = "Thank you. When did this symptom first start?"
                session.conversation.append(
                    ConversationTurn(role="assistant", text=first_dynamic_question)
                )
                repo.persist_session(session)
                await self._send_response(first_dynamic_question, session)
                return
            # else: fall through to a dynamic turn using this same utterance.

        # DYNAMIC stage — the multi-agent orchestrator (UNCHANGED).
        if session.channel_metadata.get("stage") == rt.STAGE_DYNAMIC:
            # Perceived-latency: an instant, pre-approved acknowledgment plays while
            # the gated LLM turn computes behind it (the real reply is still gated).
            if config.CR_INSTANT_ACK:
                await self._send_response(
                    self._next_instant_ack(session), session, interruptible=True
                )
            result, session = await self._run_turn(session, speech)
            action = result["action"]
            spoken = result["message"]

            if (
                not rt._is_healthcare_session(session)
                and not stability.injury_advisory_already_given(session)
                and "injuries_reported" in rt._workflow_result_flags(session)
            ):
                spoken = f"{INJURY_SAFETY_ADVISORY} {spoken}"
                stability.mark_injury_advisory_given(session)

            if action in ("finalize", "escalate"):
                await self._finalize(session, repo, result, action, spoken)
            else:
                await self._send_response(spoken, session)
            return

        logger.error(
            "[CR] unexpected state for call %s — failing closed", self.call_sid
        )
        await self._fail_closed(session, repo, reason="cr_unexpected_state")

    async def _run_turn(self, session: OrchestratorSession, text: str):
        """Run one workflow-engine turn — the SAME path the gather route uses."""
        context = rt._build_workflow_context(session)
        workflow_input = WorkflowInput(
            user_text=text,
            session_state=session.model_dump(mode="json"),
            called_phone_number=session.channel_metadata.get("called_phone_number"),
            metadata={"channel": "conversationrelay"},
        )
        turn_result = await get_workflow_engine().handle_turn(context, workflow_input)
        updated = OrchestratorSession.model_validate(turn_result.updated_state)
        get_session_repository().persist_session(updated)
        return rt._workflow_turn_to_legacy_result(turn_result), updated

    async def _run_transfer(
        self, session: OrchestratorSession, repo, user_text: str
    ) -> None:
        """Birchwood scripted transfer: run the workflow turn, speak, end."""
        session.conversation.append(ConversationTurn(role="caller", text=user_text))
        workflow_user_text = "0" if user_text != "0" else user_text
        context = rt._build_workflow_context(session)
        workflow_input = WorkflowInput(
            user_text=workflow_user_text,
            session_state=session.model_dump(mode="json"),
            called_phone_number=session.channel_metadata.get("called_phone_number"),
            metadata={
                "channel": "conversationrelay",
                "scripted_transfer_request": True,
            },
        )
        turn_result = await rt._get_workflow_for_session(session).handle_turn(
            context, workflow_input
        )
        updated = OrchestratorSession.model_validate(turn_result.updated_state)
        if not updated.is_finalized:
            updated.is_finalized = True
        if config.BIRCHWOOD_TRANSFER_NUMBER:
            # Live warm transfer (parity with the gather path): persist the
            # intake/callback record FIRST so a failed dial always has a real
            # callback record behind its promise, speak a connect line, and
            # carry the dial intent to the <Connect action> callback.
            transfer_meta = updated.channel_metadata.setdefault(
                "birchwood_transfer", {}
            )
            transfer_meta.update({"attempted": True, "source": "cr_scripted"})
            repo.persist_session(updated)
            connect_text = rt._birchwood_transfer_connect_text(
                turn_result.assistant_text,
                session=updated,
            )
            await self._send_response(connect_text, updated)
            await self._settle_final_speech(connect_text)
            await self._end(handoff={"action": "dial_birchwood"})
            return
        repo.persist_session(updated)
        await self._send_response(turn_result.assistant_text, updated)
        await self._settle_final_speech(turn_result.assistant_text)
        await self._end()

    async def _finalize(
        self,
        session: OrchestratorSession,
        repo,
        result: dict,
        action: str,
        spoken: str,
    ) -> None:
        """Speak, dispatch the real finalize()/report OUT-OF-BAND, then end —
        preserving the deferred-finalize contract (finalize() never inline)."""
        session.is_finalized = True
        rt._persist_finalization_reason_from_result(
            session,
            result,
            default_reason=(
                "workflow_error" if action == "escalate" else "sufficient_information"
            ),
        )
        # Birchwood warm transfer (conversational tier): the caller asked for
        # a human and a transfer line is configured — swap the callback close
        # for the connect line (injury advisory preserved) and dial after
        # `end`. The record persists BEFORE any dial; escalations never dial.
        birchwood_dial = (
            action == "finalize"
            and bool(config.BIRCHWOOD_TRANSFER_NUMBER)
            and rt._birchwood_transfer_requested(session)
        )
        if birchwood_dial:
            transfer_meta = session.channel_metadata.setdefault(
                "birchwood_transfer", {}
            )
            transfer_meta.update({"attempted": True, "source": "cr_conversational"})
            spoken = rt._birchwood_transfer_connect_text(spoken, session=session)
        repo.persist_session(session)

        scheduler = _AsyncTaskScheduler()
        if rt._is_healthcare_session(session):
            scheduler.add_task(
                rt._generate_orchestrator_report_background,
                session_id=session.session_id,
                orch_session=session,
                session_metadata=rt._build_session_metadata(session),
            )
        else:
            scheduler.add_task(
                rt._generate_platform_report_background,
                session_id=session.session_id,
                session=session,
            )
        rt._maybe_schedule_enrichment(scheduler, session)

        await self._send_response(spoken, session)
        # Warm transfers are performed by the <Connect action> callback
        # (/relay-action) after `end`: "dial" = nurse queue (healthcare
        # finalize + NURSE_TRANSFER_NUMBER, unchanged), "dial_birchwood" =
        # Birchwood transfer line (computed above). We decide HERE (we still
        # hold the live session) and carry only the INTENT in handoffData;
        # escalations always hang up.
        should_dial = (
            action == "finalize"
            and rt._is_healthcare_session(session)
            and bool(config.NURSE_TRANSFER_NUMBER)
        )
        if should_dial:
            handoff_action = "dial"
        elif birchwood_dial:
            handoff_action = "dial_birchwood"
        else:
            handoff_action = "hangup"
        # Let the goodbye finish playing before ending the session (CR cuts off
        # in-flight audio the moment it sees `end`). For a dial handoff this also
        # ensures the caller hears the message before the warm transfer connects.
        await self._settle_final_speech(spoken)
        await self._end(handoff={"action": handoff_action})

    async def _fail_closed(
        self, session: OrchestratorSession, repo, *, reason: str
    ) -> None:
        """Terminal fail-closed: flag the record, say goodbye, end. Never crash."""
        try:
            if not rt._is_healthcare_session(session):
                session.is_finalized = True
                if not session.finalization_reason:
                    session.finalization_reason = "system_error_fail_closed"
                session.channel_metadata["finalization_reason"] = (
                    session.finalization_reason
                )
                stability.flag_incomplete(session, reason)
                repo.persist_session(session)
        except Exception:
            logger.critical(
                "[CR] fail-closed flagging could not be persisted for call %s",
                self.call_sid,
                exc_info=True,
            )
        message = stability.fail_closed_error_message(session)
        await self._send_response(message, session)
        await self._settle_final_speech(message)
        await self._end()

    # -- output provider seam ----------------------------------------------

    async def _send_stage_prompt(
        self,
        session: OrchestratorSession,
        stage,
        *,
        preamble: str | None = None,
    ) -> None:
        rt._record_scripted_prompt_metadata(session, stage)
        prompt = rt._stage_prompt_for_session(session, stage)
        text = f"{preamble} {prompt}" if preamble else prompt
        session.conversation.append(ConversationTurn(role="assistant", text=prompt))
        get_session_repository().persist_session(session)
        await self._send_response(text, session)

    async def _send_response(
        self,
        text: str,
        session: OrchestratorSession | None,
        *,
        interruptible: bool = True,
    ) -> None:
        if not text:
            return
        if config.VOICE_OUTPUT_MODE == "cr_native":
            await self._send_text(text, interruptible=interruptible)
            return
        # azure_play (default): keep the exact Azure voice via a play URL.
        try:
            url = await rt._tts_audio_url(text, session)
        except Exception:
            logger.warning(
                "[CR] Azure TTS failed; falling back to CR text", exc_info=True
            )
            url = None
        if url:
            await self._send(
                json.dumps(
                    {"type": "play", "source": url, "interruptible": interruptible}
                )
            )
        else:
            await self._send_text(text, interruptible=interruptible)

    async def _send_text(self, text: str, *, interruptible: bool = True) -> None:
        await self._send(
            json.dumps(
                {
                    "type": "text",
                    "token": text,
                    "last": True,
                    "interruptible": interruptible,
                }
            )
        )

    def _next_instant_ack(self, session: OrchestratorSession) -> str:
        """Pick a non-repeating short acknowledgment for the instant-ack option."""
        meta = session.channel_metadata
        i = int(meta.get("cr_ack_i", -1)) + 1
        meta["cr_ack_i"] = i
        return _INSTANT_ACKS[i % len(_INSTANT_ACKS)]

    async def _settle_final_speech(self, text: str) -> None:
        """Hold long enough for the final spoken message to play before ending.

        Without this, ``_end`` ends the CR session and cuts off the goodbye —
        the caller hears silence and the call drops. Disabled (0s) in tests.
        """
        seconds = _estimate_settle_seconds(text)
        if seconds > 0:
            await asyncio.sleep(seconds)

    async def _end(self, handoff: dict | None = None) -> None:
        msg: dict = {"type": "end"}
        if handoff is not None:
            msg["handoffData"] = json.dumps(handoff)
        await self._send(json.dumps(msg))
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
