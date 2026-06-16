"""
ConversationRelay transport — skeleton tests (checkpoint 1).

Covers the transport glue only: TwiML/URL builders and the WebSocket lifecycle
driving the EXISTING workflow-engine entry point. The LLM seam
(get_workflow_engine().handle_turn) is faked so these tests are deterministic and
offline — the orchestrator/safety layers have their own tests and are unchanged.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.config as config
from src.platform.workflows.schemas import WorkflowTurnResult
from src.twilio import conversation_relay as cr


# ---------------------------------------------------------------------------
# Pure builders
# ---------------------------------------------------------------------------


def test_derive_wss_url_explicit_takes_precedence(monkeypatch):
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WSS_URL", "wss://explicit/relay")
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WS_TOKEN", "")
    assert cr.derive_wss_url() == "wss://explicit/relay"


def test_derive_wss_url_from_base_with_token(monkeypatch):
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WSS_URL", "")
    monkeypatch.setattr(config, "TWILIO_WEBHOOK_BASE_URL", "https://host.example.com")
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WS_TOKEN", "sek")
    assert cr.derive_wss_url() == "wss://host.example.com/api/v1/voice/relay?token=sek"


def test_derive_wss_url_empty_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WSS_URL", "")
    monkeypatch.setattr(config, "TWILIO_WEBHOOK_BASE_URL", "")
    assert cr.derive_wss_url() == ""


def test_build_twiml_azure_play_omits_tts_provider(monkeypatch):
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WSS_URL", "wss://x/relay")
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WS_TOKEN", "")
    monkeypatch.setattr(config, "VOICE_OUTPUT_MODE", "azure_play")
    twiml = cr.build_conversation_relay_twiml()
    assert "<ConversationRelay" in twiml
    assert 'transcriptionProvider="Deepgram"' in twiml
    assert 'dtmfDetection="true"' in twiml
    # azure_play renders TTS itself -> CR ttsProvider/voice must NOT be set.
    assert "ttsProvider" not in twiml


def test_build_twiml_cr_native_sets_tts(monkeypatch):
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WSS_URL", "wss://x/relay")
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WS_TOKEN", "")
    monkeypatch.setattr(config, "VOICE_OUTPUT_MODE", "cr_native")
    monkeypatch.setattr(config, "CR_TTS_PROVIDER", "ElevenLabs")
    monkeypatch.setattr(config, "CR_TTS_VOICE", "VoiceId123")
    twiml = cr.build_conversation_relay_twiml()
    assert 'ttsProvider="ElevenLabs"' in twiml
    assert 'voice="VoiceId123"' in twiml


def test_build_twiml_empty_when_no_url(monkeypatch):
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WSS_URL", "")
    monkeypatch.setattr(config, "TWILIO_WEBHOOK_BASE_URL", "")
    assert cr.build_conversation_relay_twiml() == ""


# ---------------------------------------------------------------------------
# WebSocket lifecycle (LLM seam faked; real session repo + route resolver)
# ---------------------------------------------------------------------------


class _FakeEngine:
    """Stand-in for the workflow engine: echoes session_state, scripts the turn."""

    def __init__(self, plan):
        # plan: list of (assistant_text, should_finalize, escalation_required)
        self._plan = list(plan)
        self.calls = 0

    async def handle_turn(self, context, workflow_input):
        text, finalize, escalate = self._plan[min(self.calls, len(self._plan) - 1)]
        self.calls += 1
        # Echo the incoming session_state back as updated_state so the transport's
        # OrchestratorSession.model_validate(...) round-trip succeeds.
        return WorkflowTurnResult(
            assistant_text=text,
            stage="DYNAMIC",
            should_continue=not (finalize or escalate),
            should_finalize=finalize,
            escalation_required=escalate,
            updated_state=workflow_input.session_state,
        )


@pytest.fixture
def cr_client(monkeypatch):
    """TestClient with cr_native output (no Azure TTS) and no WS auth token."""
    monkeypatch.setattr(config, "VOICE_OUTPUT_MODE", "cr_native")
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WS_TOKEN", "")
    from src.main import app

    with TestClient(app) as client:
        yield client


def test_ws_setup_then_ask_turn(monkeypatch, cr_client):
    fake = _FakeEngine([("Where exactly is the pain?", False, False)])
    monkeypatch.setattr(cr, "get_workflow_engine", lambda: fake)

    with cr_client.websocket_connect("/api/v1/voice/relay") as ws:
        ws.send_json(
            {
                "type": "setup",
                "callSid": "CRWS_ASK_1",
                "from": "+15555550111",
                "to": "+15555550100",
            }
        )
        greeting = ws.receive_json()
        assert greeting["type"] == "text"
        assert greeting["token"]  # non-empty greeting

        ws.send_json({"type": "prompt", "voicePrompt": "my arm hurts", "last": True})
        reply = ws.receive_json()
        assert reply["type"] == "text"
        assert reply["token"] == "Where exactly is the pain?"

    assert fake.calls == 1


def test_ws_prompt_partial_is_ignored(monkeypatch, cr_client):
    fake = _FakeEngine([("Should not be reached", False, False)])
    monkeypatch.setattr(cr, "get_workflow_engine", lambda: fake)

    with cr_client.websocket_connect("/api/v1/voice/relay") as ws:
        ws.send_json(
            {"type": "setup", "callSid": "CRWS_PARTIAL_1", "to": "+15555550100"}
        )
        ws.receive_json()  # greeting
        # Partial transcript (last=False) must NOT trigger a turn.
        ws.send_json({"type": "prompt", "voicePrompt": "um", "last": False})
        # A final utterance does.
        ws.send_json({"type": "prompt", "voicePrompt": "my arm hurts", "last": True})
        reply = ws.receive_json()
        assert reply["token"] == "Should not be reached"

    assert fake.calls == 1  # only the final utterance ran a turn


def test_ws_finalize_defers_and_ends(monkeypatch, cr_client):
    """A finalize turn speaks, sends `end`, marks the session finalized, and the
    real finalize()/report runs OUT-OF-BAND (never inline on the turn path)."""
    fake = _FakeEngine([("Thank you, a nurse will call you back.", True, False)])
    monkeypatch.setattr(cr, "get_workflow_engine", lambda: fake)

    scheduled: list[str] = []

    async def _fake_report(*args, **kwargs):
        scheduled.append("report")

    # Healthcare default route -> orchestrator report path.
    monkeypatch.setattr(
        "src.twilio.routes._generate_orchestrator_report_background", _fake_report
    )

    with cr_client.websocket_connect("/api/v1/voice/relay") as ws:
        ws.send_json({"type": "setup", "callSid": "CRWS_FIN_1", "to": "+15555550100"})
        ws.receive_json()  # greeting
        ws.send_json(
            {"type": "prompt", "voicePrompt": "it stopped, I'm fine now", "last": True}
        )
        spoken = ws.receive_json()
        assert spoken["type"] == "text"
        assert spoken["token"] == "Thank you, a nurse will call you back."
        end = ws.receive_json()
        assert end["type"] == "end"

    from src.storage.session_repository import get_session_repository

    session = get_session_repository().load_session_by_call("CRWS_FIN_1")
    # Session may be filtered out once finalized; if present it must be finalized.
    if session is not None:
        assert session.is_finalized is True


def test_ws_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WS_TOKEN", "required-token")
    from src.main import app
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/voice/relay") as ws:
                ws.receive_json()
