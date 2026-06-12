"""Birchwood pitch-polish pass — dedicated greeting, curated seed, humanization.

Locks the four polish guarantees:
  - calls to the dedicated Birchwood number NEVER hear the shared-number
    vertical menu ("for nurse triage press 1...") — they open straight into
    the collision greeting; the shared number keeps the menu unchanged
  - seeded demo transcripts open with the real dedicated-line greeting and
    contain zero references to nurse triage or other verticals
  - the curated seed set is complete (no John Doe / n/a / unknown rows),
    lands records today, and carries exactly 3 injury-flagged records
  - the safety-event display mapping covers the known collision flags and
    defaults to plain language with raw JSON behind an expander
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from scripts.seed_birchwood_demo import seed_records

BW_API = "/api/v1/dashboard/birchwood/records"
BIRCHWOOD_NUMBER = "+15555550140"
OTHER_NUMBER = "+15550001111"


def _setup_repo(mp: pytest.MonkeyPatch):
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )

    mp.setattr("src.config.STORAGE_BACKEND", "memory")
    mp.setattr("src.config.APP_ENV", "development")
    mp.setattr("src.config.ENVIRONMENT", "development")
    mp.setattr("src.config.DATABASE_URL", None)
    mp.setattr("src.config.DASHBOARD_ENABLED", True)
    reset_session_repository()
    reset_storage_backend()
    return get_session_repository()


@pytest.fixture()
def client():
    from src.main import app

    return TestClient(app, raise_server_exceptions=False)


# --- Fix 1: dedicated Birchwood entry path ----------------------------------


def test_birchwood_number_is_exempt_from_shared_vertical_menu(monkeypatch):
    from src.twilio import routes as twilio_routes

    monkeypatch.setattr("src.config.ENABLE_SHARED_NUMBER_VERTICAL_MENU", True)
    monkeypatch.setattr("src.config.SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER", "")
    monkeypatch.setattr("src.config.BIRCHWOOD_COLLISION_PHONE_NUMBER", BIRCHWOOD_NUMBER)

    # Dedicated line: menu never plays. Any other number: menu unchanged.
    assert twilio_routes._shared_vertical_menu_enabled_for(BIRCHWOOD_NUMBER) is False
    assert twilio_routes._shared_vertical_menu_enabled_for(OTHER_NUMBER) is True


def test_menu_scoping_to_one_shared_number_still_works(monkeypatch):
    from src.twilio import routes as twilio_routes

    monkeypatch.setattr("src.config.ENABLE_SHARED_NUMBER_VERTICAL_MENU", True)
    monkeypatch.setattr(
        "src.config.SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER", OTHER_NUMBER
    )
    monkeypatch.setattr("src.config.BIRCHWOOD_COLLISION_PHONE_NUMBER", BIRCHWOOD_NUMBER)

    assert twilio_routes._shared_vertical_menu_enabled_for(OTHER_NUMBER) is True
    assert twilio_routes._shared_vertical_menu_enabled_for(BIRCHWOOD_NUMBER) is False
    assert twilio_routes._shared_vertical_menu_enabled_for("+15559998888") is False


def test_dedicated_line_call_opens_with_collision_greeting_not_menu():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import reset_session_repository

    with (
        patch("src.config.STORAGE_BACKEND", "memory"),
        patch("src.config.APP_ENV", "development"),
        patch("src.config.ENVIRONMENT", "development"),
        patch("src.config.DATABASE_URL", None),
        patch("src.config.ENABLE_SHARED_NUMBER_VERTICAL_MENU", True),
        patch("src.config.SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER", ""),
        patch("src.config.BIRCHWOOD_COLLISION_PHONE_NUMBER", BIRCHWOOD_NUMBER),
        patch("src.security.twilio_signature.TWILIO_VALIDATE_SIGNATURE", False),
        patch("src.twilio.routes.text_to_speech_url", new=AsyncMock(return_value=None)),
    ):
        reset_session_repository()
        reset_storage_backend()
        from src.main import app

        client = TestClient(app, raise_server_exceptions=False)

        birchwood_call = client.post(
            "/api/v1/voice/incoming",
            data={
                "CallSid": "CA-POLISH-BW",
                "From": "+12045550100",
                "To": BIRCHWOOD_NUMBER,
            },
        )
        shared_call = client.post(
            "/api/v1/voice/incoming",
            data={
                "CallSid": "CA-POLISH-SHARED",
                "From": "+12045550101",
                "To": OTHER_NUMBER,
            },
        )

    assert birchwood_call.status_code == 200
    assert "Thank you for calling Birchwood Automotive Group" in birchwood_call.text
    lowered = birchwood_call.text.lower()
    assert "nurse triage" not in lowered
    assert "insurance claims" not in lowered
    assert "press 1" not in lowered

    # The shared number keeps the existing menu, unchanged.
    assert shared_call.status_code == 200
    assert "For nurse triage" in shared_call.text


# --- Fix 2: curated seed data -------------------------------------------------


def test_seeded_transcripts_open_with_dedicated_greeting(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup_repo(mp)
        seed_records(count=6)

        rows = client.get(f"{BW_API}?limit=10").json()["records"]
        detail = client.get(f"/api/v1/dashboard/records/{rows[0]['session_id']}").json()
        turns = detail["turns"]
        assert turns, "seeded record should carry a transcript"
        assert turns[0]["role"] == "assistant"
        assert turns[0]["text"].startswith(
            "Thank you for calling Birchwood Automotive Group"
        )
        joined = json.dumps(turns).lower()
        assert "nurse triage" not in joined
        assert "press 1" not in joined
        assert "healthcare" not in joined


def test_curated_seed_is_complete_with_no_placeholder_rows(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup_repo(mp)
        seed_records()

        data = client.get(f"{BW_API}?limit=500").json()
        rows = data["records"]
        assert data["total_matched"] == 36

        blob = json.dumps(rows)
        assert "John Doe" not in blob
        for row in rows:
            assert row["contact"]["caller_name"], "every record has a customer"
            assert row["vehicle"], "every record has a vehicle"
            assert row["collision"]["damage_type"], "every record has damage text"
            assert row["collision"]["is_drivable"] in (True, False)
            insurer_known = (
                row["collision"]["insurance_provider"]
                or row["collision"]["private_pay"]
            )
            assert insurer_known, "insurer captured (or explicitly private pay)"

        # 3 injury-flagged records, pinned by urgency.
        injuries = [r for r in rows if r["injury_flagged"]]
        assert len(injuries) == 3
        assert rows[0]["injury_flagged"] or rows[0]["urgent"]

        # Several records land today so the hero metric is non-zero.
        now = datetime.now(UTC)
        recent = [
            r
            for r in rows
            if now - datetime.fromisoformat(r["created_at"]) < timedelta(hours=24)
        ]
        assert len(recent) >= 4


# --- Fix 4: safety-event humanization ----------------------------------------


def test_safety_copy_mapping_covers_known_collision_flags():
    js = Path("src/dashboard_static/birchwood.js").read_text(encoding="utf-8")
    for flag in (
        "injuries_reported",
        "injury_advisory",
        "non_drivable_transfer",
        "glass_only_transfer",
        "caller_requested_transfer",
        "missing_claim_number",
        "possible_duplicate",
        "rebuilt_salvage_declined",
        "staff_review_rebuilt_status",
        "luxury_auto_assigned",
        "multiple_vehicles",
        "readback_correction",
    ):
        assert f"{flag}:" in js, f"SAFETY_COPY is missing friendly text for {flag}"
    # Human line by default; raw JSON only behind the expander.
    assert "Technical details" in js
    assert "humanizeSafetyEvent" in js
    assert "bw-tech-details" in js


def test_seeded_injury_record_carries_advisory_event(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup_repo(mp)
        seed_records()

        injuries = client.get(f"{BW_API}?injury_flagged=true").json()["records"]
        assert injuries
        detail = client.get(
            f"/api/v1/dashboard/records/{injuries[0]['session_id']}"
        ).json()
        flags = [
            event.get("flag")
            for event in detail["safety_events"]
            if isinstance(event, dict)
        ]
        assert "injuries_reported" in flags
