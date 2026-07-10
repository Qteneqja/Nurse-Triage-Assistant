"""Birchwood language-selection plumbing (BIRCHWOOD_FRENCH_ENABLED).

Locks the plumbing-only guarantees:
  - the flag defaults OFF, and with it off the language menu never plays —
    a Birchwood call opens with the intro exactly as before;
  - with the flag on, the call opens with the bilingual DTMF menu BEFORE
    the intro; press 1 starts the standard English call; the menu appears
    ONLY on the Birchwood scripted line;
  - the (prompt, language) catalog fails closed: an untranslated
    ``[FR TODO]`` French entry is NEVER spoken — a caller who presses 2
    hears the standard English call until real translation lands, and
    safety-critical lines resolve to the exact English copy;
  - a stray 0 (or garbage) at the language menu reprompts and then
    defaults to English — it never dials a transfer and never strands
    the caller.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import src.config as config
from src.verticals.automotive_collision import languages as bw_languages
from src.verticals.automotive_collision.prompts import (
    BIRCHWOOD_COLLISION_PROMPTS,
)

BIRCHWOOD_NUMBER = "+15555550140"
OTHER_NUMBER = "+15550001111"
TEST_TRANSFER_NUMBER = "+15555550199"


def _birchwood_client(stack: ExitStack, *, french: bool):
    """TestClient with the Birchwood dedicated line routed, memory storage,
    signature validation off, and TTS forced to the <Say> fallback."""
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )

    for target, value in [
        ("src.config.STORAGE_BACKEND", "memory"),
        ("src.config.APP_ENV", "development"),
        ("src.config.ENVIRONMENT", "development"),
        ("src.config.DATABASE_URL", None),
        ("src.config.ENABLE_SHARED_NUMBER_VERTICAL_MENU", False),
        ("src.config.BIRCHWOOD_COLLISION_PHONE_NUMBER", BIRCHWOOD_NUMBER),
        ("src.config.BIRCHWOOD_CONVERSATIONAL_INTAKE", False),
        ("src.config.BIRCHWOOD_FRENCH_ENABLED", french),
        ("src.security.twilio_signature.TWILIO_VALIDATE_SIGNATURE", False),
    ]:
        stack.enter_context(patch(target, value))
    stack.enter_context(
        patch(
            "src.twilio.routes.text_to_speech_url",
            new=AsyncMock(return_value=None),
        )
    )
    reset_session_repository()
    reset_storage_backend()
    from fastapi.testclient import TestClient

    from src.main import app

    client = TestClient(app, raise_server_exceptions=False)
    return client, get_session_repository()


def _incoming(client, call_sid="CA-LANG-1", to=BIRCHWOOD_NUMBER):
    return client.post(
        "/api/v1/voice/incoming",
        data={"CallSid": call_sid, "From": "+12045550100", "To": to},
    )


def _gather(client, call_sid="CA-LANG-1", digits=None, speech=None):
    data = {"CallSid": call_sid, "To": BIRCHWOOD_NUMBER}
    if digits is not None:
        data["Digits"] = digits
    if speech is not None:
        data["SpeechResult"] = speech
    return client.post("/api/v1/voice/gather", data=data)


# ---------------------------------------------------------------------------
# The flag and its default
# ---------------------------------------------------------------------------


def test_french_flag_defaults_off():
    assert config.BIRCHWOOD_FRENCH_ENABLED is False


def test_flag_off_language_menu_never_plays():
    with ExitStack() as stack:
        client, _repo = _birchwood_client(stack, french=False)
        response = _incoming(client)
    assert response.status_code == 200
    # The call opens with the intro exactly as before — no menu step.
    assert "Thank you for calling Birchwood Automotive Group" in response.text
    assert "Pour le fran" not in response.text
    assert "press 1" not in response.text.lower()


# ---------------------------------------------------------------------------
# Flag on: the menu plays first, and only on the Birchwood scripted line
# ---------------------------------------------------------------------------


def test_flag_on_menu_plays_before_intro():
    with ExitStack() as stack:
        client, repo = _birchwood_client(stack, french=True)
        response = _incoming(client)
        session = repo.load_session_by_call("CA-LANG-1")
    assert response.status_code == 200
    assert "For English, press 1" in response.text
    assert "Pour le fran" in response.text
    # DTMF-only single-digit gather; the intro has NOT played yet.
    assert 'input="dtmf"' in response.text
    assert 'numDigits="1"' in response.text
    assert "Thank you for calling Birchwood Automotive Group" not in response.text
    assert session.channel_metadata["stage"] == "BIRCHWOOD_LANGUAGE_MENU"


def test_flag_on_menu_only_on_birchwood_line():
    with ExitStack() as stack:
        client, _repo = _birchwood_client(stack, french=True)
        response = _incoming(client, call_sid="CA-LANG-HC", to=OTHER_NUMBER)
    assert response.status_code == 200
    assert "Pour le fran" not in response.text


def test_press_1_starts_the_standard_english_call():
    with ExitStack() as stack:
        client, repo = _birchwood_client(stack, french=True)
        _incoming(client)
        response = _gather(client, digits="1")
        session = repo.load_session_by_call("CA-LANG-1")
    assert response.status_code == 200
    # Intro (disclosure + recording clause + press-zero handoff) then the
    # first scripted question — the same call a flag-off caller gets.
    assert "Thank you for calling Birchwood Automotive Group" in response.text
    assert "just press zero" in response.text
    assert BIRCHWOOD_COLLISION_PROMPTS["caller_name"] in response.text
    assert bw_languages.FR_TODO_MARKER not in response.text
    assert session.channel_metadata["language"] == "en"
    assert session.channel_metadata["stage"] == "CALLER_NAME"


def test_press_2_fails_closed_to_english_no_placeholder_spoken():
    with ExitStack() as stack:
        client, repo = _birchwood_client(stack, french=True)
        _incoming(client)
        response = _gather(client, digits="2")
        session = repo.load_session_by_call("CA-LANG-1")
    assert response.status_code == 200
    # French was requested and recorded, but the catalog is untranslated —
    # the caller hears the standard ENGLISH call, never "[FR TODO]" copy.
    assert session.channel_metadata["language"] == "fr"
    assert bw_languages.FR_TODO_MARKER not in response.text
    assert "Thank you for calling Birchwood Automotive Group" in response.text
    assert BIRCHWOOD_COLLISION_PROMPTS["caller_name"] in response.text


def test_menu_zero_reprompts_and_never_dials():
    with ExitStack() as stack:
        stack.enter_context(
            patch("src.config.BIRCHWOOD_TRANSFER_NUMBER", TEST_TRANSFER_NUMBER)
        )
        client, _repo = _birchwood_client(stack, french=True)
        _incoming(client)
        response = _gather(client, digits="0")
    assert response.status_code == 200
    assert "<Dial" not in response.text
    assert "For English, press 1" in response.text


def test_menu_defaults_to_english_after_repeated_invalid_input():
    with ExitStack() as stack:
        client, repo = _birchwood_client(stack, french=True)
        _incoming(client)
        first = _gather(client, digits="9")
        second = _gather(client, digits="9")
        session = repo.load_session_by_call("CA-LANG-1")
    # One reprompt, then the call proceeds in English — never stranded.
    assert "For English, press 1" in first.text
    assert "Thank you for calling Birchwood Automotive Group" in second.text
    assert session.channel_metadata["language"] == "en"
    assert session.channel_metadata["language_selection"]["defaulted"] is True


# ---------------------------------------------------------------------------
# The (prompt, language) catalog — parity + fail-closed lookup
# ---------------------------------------------------------------------------


def test_catalog_language_parity_and_english_fully_populated():
    en = bw_languages.PROMPT_CATALOG[bw_languages.LANGUAGE_ENGLISH]
    fr = bw_languages.PROMPT_CATALOG[bw_languages.LANGUAGE_FRENCH]
    assert set(en) == set(fr)
    # Every scripted stage prompt is in the catalog.
    assert set(BIRCHWOOD_COLLISION_PROMPTS) <= set(en)
    for key, text in en.items():
        assert text.strip(), f"empty English copy for {key}"
        assert not bw_languages.is_untranslated_placeholder(text), key


def test_get_prompt_never_returns_a_placeholder_in_any_language():
    for key in bw_languages.PROMPT_CATALOG[bw_languages.LANGUAGE_ENGLISH]:
        for language in bw_languages.SUPPORTED_LANGUAGES:
            spoken = bw_languages.get_prompt(key, language)
            assert bw_languages.FR_TODO_MARKER not in spoken, (key, language)
            assert spoken.strip(), (key, language)


def test_safety_critical_lines_resolve_to_exact_english_while_untranslated():
    for key in bw_languages.SAFETY_CRITICAL_PROMPT_KEYS:
        english = bw_languages.get_prompt(key, bw_languages.LANGUAGE_ENGLISH)
        assert bw_languages.get_prompt(key, bw_languages.LANGUAGE_FRENCH) == english


def test_get_prompt_unknown_key_is_a_programming_error():
    try:
        bw_languages.get_prompt("no_such_prompt_key", "en")
    except KeyError:
        return
    raise AssertionError("unknown prompt key must raise KeyError")


def test_resolve_language_choice_mapping():
    resolve = bw_languages.resolve_language_choice
    assert resolve(None, "1") == "en"
    assert resolve(None, "2") == "fr"
    assert resolve("English please", None) == "en"
    assert resolve("le français", None) == "fr"
    assert resolve("francais", None) == "fr"
    # Number words are deliberately NOT matched ("two"/"tow" STT confusion),
    # and unrelated digits/speech resolve to nothing.
    assert resolve("two", None) is None
    assert resolve(None, "0") is None
    assert resolve(None, "9") is None
    assert resolve("", None) is None
    assert resolve(None, None) is None


# ---------------------------------------------------------------------------
# (stage, language) lookup through the workflow — fail-closed + future French
# ---------------------------------------------------------------------------


def _fr_session():
    from src.orchestrator.schemas import OrchestratorSession
    from src.verticals.automotive_collision.constants import (
        BIRCHWOOD_COLLISION_WORKFLOW_ID,
    )

    session = OrchestratorSession(session_id="lang-test", call_sid="lang-test")
    session.workflow_id = BIRCHWOOD_COLLISION_WORKFLOW_ID
    session.channel_metadata["scripted_intake"] = {"fields": {}}
    bw_languages.set_session_language(session, "fr")
    return session


def _caller_name_stage():
    from src.verticals.automotive_collision.workflow import (
        BirchwoodCollisionIntakeWorkflow,
    )

    workflow = BirchwoodCollisionIntakeWorkflow()
    intake = workflow.get_scripted_intake_definition()
    stage = next(s for s in intake.stages if s.field_name == "caller_name")
    return workflow, stage


def test_stage_prompt_lookup_falls_back_to_english_while_untranslated():
    workflow, stage = _caller_name_stage()
    prompt = workflow.build_dynamic_prompt(_fr_session(), stage)
    assert prompt is None or bw_languages.FR_TODO_MARKER not in prompt


def test_stage_prompt_lookup_uses_a_real_translation_when_present(monkeypatch):
    workflow, stage = _caller_name_stage()
    translated = "Quel est votre nom complet, s'il vous plaît?"
    monkeypatch.setitem(
        bw_languages.PROMPT_CATALOG[bw_languages.LANGUAGE_FRENCH],
        "caller_name",
        translated,
    )
    assert workflow.build_dynamic_prompt(_fr_session(), stage) == translated


def test_final_messages_fail_closed_to_english_for_french_session():
    from src.verticals.automotive_collision.workflow import _spoken_final_message

    final_result = SimpleNamespace(structured_output={})
    for disposition in [
        "TRANSFER_COLLISION_CENTER",
        "DECLINED_VEHICLE_YEAR",
        "INCOMPLETE_CALLBACK_NEEDED",
        "HUMAN_REVIEW",
    ]:
        spoken = _spoken_final_message(disposition, final_result, _fr_session())
        assert bw_languages.FR_TODO_MARKER not in spoken
        assert spoken == _spoken_final_message(disposition, final_result, None)
