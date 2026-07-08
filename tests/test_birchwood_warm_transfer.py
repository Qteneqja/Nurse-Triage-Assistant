"""Birchwood live warm transfer (BIRCHWOOD_TRANSFER_NUMBER).

Covers the config-driven <Dial> path added for the pilot:

* number SET  -> "transfer"/press-0 fires <Dial> to the CONFIG number (never
  a number from the call payload), with the dial-status action callback;
* number UNSET -> the existing callback-capture close (no <Dial>, no dead or
  broken dial);
* dial busy/no-answer/failed -> the <Dial action> callback speaks the honest
  callback-fallback copy and hangs up (never dead air);
* the healthcare <Dial>/NURSE_TRANSFER_NUMBER path is byte-unchanged;
* the automated-assistant + recording disclosure stays in the intro;
* a spoken injury advisory is never dropped by the transfer substitution.

Deterministic — no LLM, no Twilio, no engine/gate change.
"""

from __future__ import annotations

import asyncio

from src.orchestrator.schemas import OrchestratorSession
from src.safety.injury_detection import INJURY_SAFETY_ADVISORY
from src.twilio import routes as rt
from src.verticals.automotive_collision.constants import (
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.automotive_collision.prompts import (
    BIRCHWOOD_COLLISION_INTRO,
    BIRCHWOOD_TRANSFER_CONNECTING_MESSAGE,
    BIRCHWOOD_TRANSFER_DIAL_FALLBACK_MESSAGE,
)

TEST_TRANSFER_NUMBER = "+15005550006"  # Twilio magic test number, not real


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self, session: OrchestratorSession | None = None):
        self.persisted: list[OrchestratorSession] = []
        self._session = session

    def persist_session(self, session):
        self.persisted.append(session)

    def load_session_by_call(self, call_sid):
        return self._session


def _birchwood_session() -> OrchestratorSession:
    session = OrchestratorSession(session_id="warm-test", call_sid="CA-warm-test")
    session.workflow_id = BIRCHWOOD_COLLISION_WORKFLOW_ID
    session.channel_metadata["stage"] = "CALLER_NAME"
    session.channel_metadata["scripted_intake"] = {"fields": {}, "completed": False}
    return session


async def _no_tts(text, session=None):
    return None


def _patch_common(monkeypatch):
    monkeypatch.setattr(rt, "_tts_audio_url", _no_tts)
    monkeypatch.setattr("src.config.BIRCHWOOD_CONVERSATIONAL_INTAKE", False)


# ---------------------------------------------------------------------------
# TwiML generator: config number only, action callback wired
# ---------------------------------------------------------------------------


def test_transfer_twiml_dials_config_number_with_action(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", TEST_TRANSFER_NUMBER)
    twiml = asyncio.run(
        rt.generate_twiml_birchwood_transfer("connecting", _birchwood_session())
    )
    assert f">{TEST_TRANSFER_NUMBER}</Dial>" in twiml
    assert 'action="/api/v1/voice/birchwood-dial-status"' in twiml
    assert "connecting" in twiml
    # The fallback promise is spoken only by the action callback, never
    # alongside the dial (no double-speak).
    assert BIRCHWOOD_TRANSFER_DIAL_FALLBACK_MESSAGE not in twiml


def test_transfer_twiml_without_number_degrades_to_hangup(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", "")
    twiml = asyncio.run(
        rt.generate_twiml_birchwood_transfer("hello", _birchwood_session())
    )
    assert "<Dial" not in twiml
    assert "<Hangup/>" in twiml


# ---------------------------------------------------------------------------
# Scripted path: "transfer" / press-0
# ---------------------------------------------------------------------------


def test_scripted_transfer_with_number_fires_dial(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", TEST_TRANSFER_NUMBER)
    session = _birchwood_session()
    repo = _FakeRepo()
    twiml = asyncio.run(
        rt._handle_birchwood_scripted_transfer_request(
            session=session,
            repo=repo,
            request=None,
            user_text="0",
        )
    )
    assert f">{TEST_TRANSFER_NUMBER}</Dial>" in twiml
    assert 'action="/api/v1/voice/birchwood-dial-status"' in twiml
    assert BIRCHWOOD_TRANSFER_CONNECTING_MESSAGE in twiml
    # The record is persisted BEFORE the dial, so the fallback's callback
    # promise is always backed by a real record.
    assert repo.persisted, "intake record must be persisted before dialing"
    persisted = repo.persisted[-1]
    assert persisted.channel_metadata["birchwood_transfer"]["attempted"] is True


def test_scripted_transfer_number_never_from_payload(monkeypatch):
    """A caller-spoken number must never become the dial target."""
    _patch_common(monkeypatch)
    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", TEST_TRANSFER_NUMBER)
    session = _birchwood_session()
    twiml = asyncio.run(
        rt._handle_birchwood_scripted_transfer_request(
            session=session,
            repo=_FakeRepo(),
            request=None,
            user_text="transfer me to 204-555-0199 right now",
        )
    )
    assert f">{TEST_TRANSFER_NUMBER}</Dial>" in twiml
    assert "204-555-0199" not in twiml
    assert "2045550199" not in twiml


def test_scripted_transfer_without_number_routes_to_callback(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", "")
    session = _birchwood_session()
    repo = _FakeRepo()
    twiml = asyncio.run(
        rt._handle_birchwood_scripted_transfer_request(
            session=session,
            repo=repo,
            request=None,
            user_text="0",
        )
    )
    assert "<Dial" not in twiml
    assert "<Hangup/>" in twiml
    # Honest copy: a callback close, not a transfer promise.
    assert BIRCHWOOD_TRANSFER_CONNECTING_MESSAGE not in twiml
    assert repo.persisted


# ---------------------------------------------------------------------------
# Dial-status action callback: never strand the caller
# ---------------------------------------------------------------------------


def _run_dial_status(monkeypatch, status, session):
    monkeypatch.setattr(rt, "get_session_repository", lambda: _FakeRepo(session))
    response = asyncio.run(
        rt.handle_birchwood_dial_status(
            request=None,
            CallSid="CA-warm-test",
            DialCallStatus=status,
        )
    )
    return response.body.decode()


def test_dial_failure_falls_back_to_honest_callback_copy(monkeypatch):
    _patch_common(monkeypatch)
    for status in ("busy", "no-answer", "failed", "canceled", None):
        body = _run_dial_status(monkeypatch, status, _birchwood_session())
        assert BIRCHWOOD_TRANSFER_DIAL_FALLBACK_MESSAGE in body, status
        assert "<Hangup/>" in body, status


def test_dial_completed_hangs_up_without_fallback_copy(monkeypatch):
    _patch_common(monkeypatch)
    body = _run_dial_status(monkeypatch, "completed", _birchwood_session())
    assert "<Hangup/>" in body
    assert BIRCHWOOD_TRANSFER_DIAL_FALLBACK_MESSAGE not in body


def test_dial_failure_with_no_session_still_speaks_fallback(monkeypatch):
    _patch_common(monkeypatch)
    body = _run_dial_status(monkeypatch, "no-answer", None)
    assert BIRCHWOOD_TRANSFER_DIAL_FALLBACK_MESSAGE in body
    assert "<Hangup/>" in body


# ---------------------------------------------------------------------------
# Healthcare path byte-unchanged
# ---------------------------------------------------------------------------


def test_healthcare_handoff_twiml_is_byte_unchanged(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr("src.config.NURSE_TRANSFER_NUMBER", "+15005550009")
    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", TEST_TRANSFER_NUMBER)
    twiml = asyncio.run(rt.generate_twiml_handoff("goodbye"))
    expected = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f'    <Say voice="{rt._TTS_VOICE}">goodbye</Say>\n'
        "    <Dial>+15005550009</Dial>\n"
        "</Response>"
    )
    assert twiml == expected
    # No Birchwood action URL, number, or copy leaks into the nurse path.
    assert "birchwood-dial-status" not in twiml
    assert TEST_TRANSFER_NUMBER not in twiml


# ---------------------------------------------------------------------------
# Conversational-tier detection helper
# ---------------------------------------------------------------------------


def test_transfer_requested_matches_conversational_reason():
    session = _birchwood_session()
    session.finalization_reason = "automotive_collision_conversational_transfer_request"
    assert rt._birchwood_transfer_requested(session) is True


def test_transfer_requested_false_for_other_reasons_and_verticals():
    session = _birchwood_session()
    session.finalization_reason = "automotive_collision_intake_complete"
    assert rt._birchwood_transfer_requested(session) is False

    healthcare = OrchestratorSession(session_id="hc", call_sid="CA-hc")
    healthcare.workflow_id = "healthcare_triage_v1"
    healthcare.finalization_reason = "anything_transfer_request"
    assert rt._birchwood_transfer_requested(healthcare) is False
    assert rt._birchwood_transfer_requested(None) is False


# ---------------------------------------------------------------------------
# Live-call regression: transfer intent must survive real STT output
# (Field report: caller said "transfer", STT decorated it, detection missed,
# and "Transfer" was captured as the caller's NAME.)
# ---------------------------------------------------------------------------


def test_transfer_detected_across_real_stt_variants():
    session = _birchwood_session()
    for utterance in (
        "transfer",
        "Transfer.",
        "Transfer, please.",
        "TRANSFER",
        "Can you transfer me?",
        "I want to be transferred",
        "please transfer me to a person",
        "Transferring me to someone would be great",
        "Zero.",
        "I'd like to talk to someone",
        "get me a real person",
    ):
        assert rt._is_birchwood_scripted_transfer_request(session, utterance, None), (
            utterance
        )


def test_transfer_not_triggered_by_normal_answers():
    session = _birchwood_session()
    for utterance in (
        # "transfer case" is a drivetrain part, not a transfer request.
        "The transfer case is cracked and leaking.",
        "My name is Johnathan Smithers",
        "Rear bumper is dented",
        # Dictating a phone number must never divert the call.
        "two zero four eight nine zero one six eight four",
        "204 555 0142",
    ):
        assert not rt._is_birchwood_scripted_transfer_request(
            session, utterance, None
        ), utterance


def test_transfer_words_can_never_be_captured_as_a_name():
    for word in ("Transfer.", "Transfer", "transferred", "Operator.", "Agent"):
        assert rt._looks_like_name(word) is False, word
    # Real names still pass.
    assert rt._looks_like_name("Johnathan") is True
    assert rt._looks_like_name("Test Patient, Ten") is True


def test_transfer_detection_still_defers_to_conversational_stage():
    session = _birchwood_session()
    session.channel_metadata["stage"] = "DYNAMIC"
    # The conversational tier owns transfer detection in DYNAMIC.
    assert not rt._is_birchwood_scripted_transfer_request(session, "transfer", None)


# ---------------------------------------------------------------------------
# ConversationRelay transport parity (the LIVE pilot line runs
# VOICE_PIPELINE=conversation_relay + BIRCHWOOD_CONVERSATIONAL_INTAKE=true:
# a transfer request must dial there too, not say-goodbye-and-hangup)
# ---------------------------------------------------------------------------


def test_relay_action_dials_birchwood_number_with_fallback_callback(monkeypatch):
    import json as _json

    from fastapi.testclient import TestClient

    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", TEST_TRANSFER_NUMBER)
    from src.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/voice/relay-action",
            data={
                "CallSid": "CX-BW",
                "HandoffData": _json.dumps({"action": "dial_birchwood"}),
            },
        )
    assert resp.status_code == 200
    assert f">{TEST_TRANSFER_NUMBER}</Dial>" in resp.text
    assert 'action="/api/v1/voice/birchwood-dial-status"' in resp.text


def test_relay_action_hangs_up_for_birchwood_without_number(monkeypatch):
    import json as _json

    from fastapi.testclient import TestClient

    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", "")
    from src.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/voice/relay-action",
            data={
                "CallSid": "CX-BW2",
                "HandoffData": _json.dumps({"action": "dial_birchwood"}),
            },
        )
    assert resp.status_code == 200
    assert "<Hangup/>" in resp.text
    assert "<Dial" not in resp.text


def test_relay_action_never_dials_a_payload_number(monkeypatch):
    """handoffData carries intent only — a number smuggled into the payload
    must never become the dial target."""
    import json as _json

    from fastapi.testclient import TestClient

    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", TEST_TRANSFER_NUMBER)
    from src.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/voice/relay-action",
            data={
                "CallSid": "CX-BW3",
                "HandoffData": _json.dumps(
                    {"action": "dial_birchwood", "number": "+19998887777"}
                ),
            },
        )
    assert "+19998887777" not in resp.text
    assert f">{TEST_TRANSFER_NUMBER}</Dial>" in resp.text


def _cr_session_handler(monkeypatch):
    """A ConversationRelaySession with the WS/speech seams stubbed so
    _finalize's intent decision can be exercised directly."""
    from src.twilio.conversation_relay import ConversationRelaySession

    handler = ConversationRelaySession.__new__(ConversationRelaySession)
    handler.call_sid = "CA-cr-test"
    handler.session_id = "cr-test"
    handler.closed = False
    calls = {"spoken": [], "handoff": None}

    async def _send_response(text, session=None, **kwargs):
        calls["spoken"].append(text)

    async def _settle(text):
        pass

    async def _end(handoff=None):
        calls["handoff"] = handoff

    handler._send_response = _send_response
    handler._settle_final_speech = _settle
    handler._end = _end
    return handler, calls


def test_cr_conversational_transfer_finalize_carries_dial_intent(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", TEST_TRANSFER_NUMBER)
    monkeypatch.setattr(rt, "_maybe_schedule_enrichment", lambda *a, **k: None)
    handler, calls = _cr_session_handler(monkeypatch)

    session = _birchwood_session()
    session.finalization_reason = "automotive_collision_conversational_transfer_request"
    repo = _FakeRepo()
    asyncio.run(
        handler._finalize(
            session,
            repo,
            result={},
            action="finalize",
            spoken="Thanks, an advisor will call you back.",
        )
    )
    assert calls["handoff"] == {"action": "dial_birchwood"}
    # Connect copy replaces the callback close before the dial.
    assert BIRCHWOOD_TRANSFER_CONNECTING_MESSAGE in calls["spoken"][0]
    assert repo.persisted
    persisted = repo.persisted[-1]
    assert persisted.channel_metadata["birchwood_transfer"]["attempted"] is True


def test_cr_finalize_without_transfer_request_hangs_up(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", TEST_TRANSFER_NUMBER)
    monkeypatch.setattr(rt, "_maybe_schedule_enrichment", lambda *a, **k: None)
    handler, calls = _cr_session_handler(monkeypatch)

    session = _birchwood_session()
    session.finalization_reason = "automotive_collision_intake_complete"
    asyncio.run(
        handler._finalize(
            session,
            repo=_FakeRepo(),
            result={},
            action="finalize",
            spoken="Thanks, you're all set.",
        )
    )
    assert calls["handoff"] == {"action": "hangup"}
    assert BIRCHWOOD_TRANSFER_CONNECTING_MESSAGE not in calls["spoken"][0]


def test_cr_escalation_never_dials(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr("src.config.BIRCHWOOD_TRANSFER_NUMBER", TEST_TRANSFER_NUMBER)
    monkeypatch.setattr(rt, "_maybe_schedule_enrichment", lambda *a, **k: None)
    handler, calls = _cr_session_handler(monkeypatch)

    session = _birchwood_session()
    session.finalization_reason = "automotive_collision_conversational_transfer_request"
    asyncio.run(
        handler._finalize(
            session,
            repo=_FakeRepo(),
            result={},
            action="escalate",
            spoken="Please seek medical attention.",
        )
    )
    assert calls["handoff"] == {"action": "hangup"}


# ---------------------------------------------------------------------------
# Safety invariants: disclosure intact, injury advisory never dropped
# ---------------------------------------------------------------------------


def test_intro_disclosure_and_transfer_offer_intact():
    assert "automated assistant" in BIRCHWOOD_COLLISION_INTRO
    assert "recorded for training and quality purposes" in BIRCHWOOD_COLLISION_INTRO
    assert "say transfer or press zero" in BIRCHWOOD_COLLISION_INTRO


def test_connect_text_preserves_injury_advisory():
    final_text = f"{INJURY_SAFETY_ADVISORY} Thanks, we'll call you back."
    connect = rt._birchwood_transfer_connect_text(final_text)
    assert connect.startswith(INJURY_SAFETY_ADVISORY)
    assert BIRCHWOOD_TRANSFER_CONNECTING_MESSAGE in connect

    plain = rt._birchwood_transfer_connect_text("Thanks, we'll call you back.")
    assert plain == BIRCHWOOD_TRANSFER_CONNECTING_MESSAGE
