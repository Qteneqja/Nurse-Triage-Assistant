"""
ConversationRelay transport tests.

Covers the transport glue only: TwiML/URL builders, the <Connect action> handoff
callback, and the WebSocket lifecycle driving the EXISTING workflow-engine entry
point through the re-hosted scripted-intake state machine. The LLM seam
(get_workflow_engine().handle_turn) is faked so these tests are deterministic and
offline — the orchestrator/safety layers have their own tests and are unchanged.

Healthcare scripted intake order: NAME -> AGE -> SEX -> CHIEF_COMPLAINT -> DYNAMIC.
"""

from __future__ import annotations

import json

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


def test_build_twiml_azure_play_omits_tts_and_sets_action(monkeypatch):
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WSS_URL", "")
    monkeypatch.setattr(config, "TWILIO_WEBHOOK_BASE_URL", "https://host.example.com")
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WS_TOKEN", "")
    monkeypatch.setattr(config, "VOICE_OUTPUT_MODE", "azure_play")
    twiml = cr.build_conversation_relay_twiml()
    assert "<ConversationRelay" in twiml
    assert 'transcriptionProvider="Deepgram"' in twiml
    assert 'dtmfDetection="true"' in twiml
    assert "ttsProvider" not in twiml  # azure renders TTS itself
    # handoff callback wired on <Connect>
    assert "/api/v1/voice/relay-action" in twiml


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


def _drive_scripted_intake(ws, call_sid):
    """Setup + answer NAME/AGE/SEX/CHIEF_COMPLAINT; return the message that
    follows the chief-complaint answer (the first DYNAMIC response)."""
    ws.send_json({"type": "setup", "callSid": call_sid, "to": "+15555550100"})
    greeting = ws.receive_json()
    assert greeting["type"] == "text" and greeting["token"]
    name_q = ws.receive_json()
    assert name_q["type"] == "text" and name_q["token"]

    for answer in ("John Smith", "45", "male"):
        ws.send_json({"type": "prompt", "voicePrompt": answer, "last": True})
        nxt = ws.receive_json()
        assert nxt["type"] == "text" and nxt["token"]

    ws.send_json({"type": "prompt", "voicePrompt": "my chest hurts", "last": True})
    return ws.receive_json()


def test_ws_scripted_intake_then_dynamic_ask(monkeypatch, cr_client):
    fake = _FakeEngine([("Where exactly is the pain?", False, False)])
    monkeypatch.setattr(cr, "get_workflow_engine", lambda: fake)

    with cr_client.websocket_connect("/api/v1/voice/relay") as ws:
        dyn = _drive_scripted_intake(ws, "CRWS_SCRIPTED_1")
        assert dyn["type"] == "text"
        assert dyn["token"] == "Where exactly is the pain?"

    # Engine is only invoked for the DYNAMIC turn, never during scripted intake.
    assert fake.calls == 1


def test_ws_partial_prompt_ignored_during_scripted(monkeypatch, cr_client):
    fake = _FakeEngine([("unused", False, False)])
    monkeypatch.setattr(cr, "get_workflow_engine", lambda: fake)

    with cr_client.websocket_connect("/api/v1/voice/relay") as ws:
        ws.send_json(
            {"type": "setup", "callSid": "CRWS_PARTIAL_1", "to": "+15555550100"}
        )
        ws.receive_json()  # greeting
        name_q = ws.receive_json()  # NAME prompt
        assert name_q["token"]
        # Partial transcript must NOT advance the scripted stage.
        ws.send_json({"type": "prompt", "voicePrompt": "Joh", "last": False})
        # Final answer advances to AGE.
        ws.send_json({"type": "prompt", "voicePrompt": "John Smith", "last": True})
        age_q = ws.receive_json()
        assert age_q["type"] == "text" and age_q["token"]

    assert fake.calls == 0  # scripted intake never calls the engine


def test_ws_finalize_defers_and_ends_with_dial_intent(monkeypatch, cr_client):
    """A finalize turn speaks, sends `end` (carrying the dial intent), and the
    real finalize()/report runs OUT-OF-BAND (never inline on the turn path)."""
    monkeypatch.setattr(config, "NURSE_TRANSFER_NUMBER", "+18005551234")
    fake = _FakeEngine([("Thank you, a nurse will call you back.", True, False)])
    monkeypatch.setattr(cr, "get_workflow_engine", lambda: fake)

    async def _fake_report(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "src.twilio.routes._generate_orchestrator_report_background", _fake_report
    )

    with cr_client.websocket_connect("/api/v1/voice/relay") as ws:
        spoken = _drive_scripted_intake(ws, "CRWS_FIN_1")
        assert spoken["type"] == "text"
        assert spoken["token"] == "Thank you, a nurse will call you back."
        end = ws.receive_json()
        assert end["type"] == "end"
        # Healthcare finalize + NURSE_TRANSFER_NUMBER -> dial intent for the
        # /relay-action handoff callback.
        assert json.loads(end["handoffData"])["action"] == "dial"


def test_ws_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(config, "CONVERSATION_RELAY_WS_TOKEN", "required-token")
    from src.main import app
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/voice/relay") as ws:
                ws.receive_json()


# ---------------------------------------------------------------------------
# Handoff callback (/relay-action)
# ---------------------------------------------------------------------------


def test_relay_action_dials_on_dial_intent(monkeypatch):
    monkeypatch.setattr(config, "NURSE_TRANSFER_NUMBER", "+18005551234")
    from src.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/voice/relay-action",
            data={"CallSid": "CX1", "HandoffData": json.dumps({"action": "dial"})},
        )
    assert resp.status_code == 200
    assert "<Dial>+18005551234</Dial>" in resp.text


def test_relay_action_hangs_up_without_dial_intent(monkeypatch):
    monkeypatch.setattr(config, "NURSE_TRANSFER_NUMBER", "+18005551234")
    from src.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/voice/relay-action",
            data={"CallSid": "CX2", "HandoffData": json.dumps({"action": "hangup"})},
        )
    assert resp.status_code == 200
    assert "<Hangup/>" in resp.text
    assert "<Dial>" not in resp.text


def test_relay_action_hangs_up_when_no_transfer_number(monkeypatch):
    monkeypatch.setattr(config, "NURSE_TRANSFER_NUMBER", "")
    from src.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/voice/relay-action",
            data={"CallSid": "CX3", "HandoffData": json.dumps({"action": "dial"})},
        )
    assert resp.status_code == 200
    assert "<Hangup/>" in resp.text
