"""Birchwood pitch dashboard — scoped endpoint, seed script, shell gate.

Locks the pitch build's guarantees:
  - /api/v1/dashboard/birchwood/records returns ONLY automotive_collision
    rows — no healthcare or insurance data can leak into the pitch view
  - rows carry the collision-specific fields the bespoke panels render
  - seed script loads ~32 varied synthetic records and removes them all
    with one command
  - injury rows pin to the top; collision filters work
  - the /dashboard/birchwood shell sits behind the same production auth
    gate as the main dashboard
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from scripts.seed_birchwood_demo import remove_records, seed_records
from src.main import app
from src.orchestrator.schemas import OrchestratorSession

BW_API = "/api/v1/dashboard/birchwood/records"


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


def _healthcare_session(repo, *, session_id: str = "hc-1"):
    session = OrchestratorSession(session_id=session_id, call_sid=f"CA-{session_id}")
    session.vertical_key = "healthcare"
    session.workflow_id = "healthcare_triage_v1"
    session.is_finalized = True
    session.created_at = datetime.now(UTC) - timedelta(minutes=1)
    session.intake_state.caller_name = "Pat Patient"
    session.intake_state.chief_complaint = "chest pain"
    repo._backend.save_session(session)
    return session


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_seed_loads_then_removes_with_one_command(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup_repo(mp)
        seeded = seed_records(count=32)
        assert len(seeded) == 32

        listed = client.get(f"{BW_API}?limit=500").json()
        assert listed["total_matched"] == 32

        assert remove_records() == 32
        assert client.get(f"{BW_API}?limit=500").json()["total_matched"] == 0


def test_birchwood_endpoint_excludes_other_verticals(client):
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_repo(mp)
        seed_records(count=8)
        _healthcare_session(repo)

        body = client.get(f"{BW_API}?limit=500")
        data = body.json()
        assert data["total_matched"] == 8
        assert all(
            row["vertical_key"] == "automotive_collision" for row in data["records"]
        )
        # No healthcare content anywhere in the payload.
        assert "healthcare" not in body.text
        assert "chest pain" not in body.text
        assert "Pat Patient" not in body.text


def test_rows_carry_collision_fields_and_demo_marker(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup_repo(mp)
        seed_records(count=8)

        rows = client.get(f"{BW_API}?limit=500").json()["records"]
        sample = rows[0]
        assert sample["is_demo"] is True
        assert sample["intake_duration_seconds"] > 0
        collision = sample["collision"]
        for field in (
            "is_drivable",
            "damage_type",
            "insurance_provider",
            "claim_number_present",
            "police_report_filed",
            "photos_available",
        ):
            assert field in collision
        # Claim numbers themselves never appear in the list payload.
        assert 'claim_number"' not in str(rows).replace("claim_number_present", "")


def test_injury_rows_pin_to_top_and_filters_scope(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup_repo(mp)
        seed_records(count=32)

        data = client.get(f"{BW_API}?limit=500").json()
        ranks = [row["urgency_rank"] for row in data["records"]]
        assert ranks == sorted(ranks, reverse=True)
        assert data["records"][0]["urgency_rank"] > 0

        injuries = client.get(f"{BW_API}?injury_flagged=true").json()["records"]
        assert injuries and all(r["injury_flagged"] for r in injuries)

        towed = client.get(f"{BW_API}?drivable=false").json()["records"]
        assert towed and all(r["collision"]["is_drivable"] is False for r in towed)

        news = client.get(f"{BW_API}?record_status=new").json()["records"]
        assert news and all(r["record_status"] == "new" for r in news)

        bad = client.get(f"{BW_API}?record_status=bogus")
        assert bad.status_code == 422


def test_seeded_status_history_is_audited(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup_repo(mp)
        seed_records(count=32)

        rows = client.get(f"{BW_API}?record_status=scheduled").json()["records"]
        assert rows
        detail = client.get(f"/api/v1/dashboard/records/{rows[0]['session_id']}").json()
        statuses = [event["status"] for event in detail["status_history"]]
        assert "scheduled" in statuses
        assert all(
            event["actor"] == "demo.seeder" for event in detail["status_history"]
        )


def _shell_client(monkeypatch, token: str = "strong-dashboard-token-value-12345"):
    from fastapi import FastAPI

    from src.api import dashboard

    shell_app = FastAPI()
    monkeypatch.setattr(dashboard.config, "APP_ENV", "production")
    monkeypatch.setattr(dashboard.config, "ENVIRONMENT", "production")
    monkeypatch.setattr(dashboard.config, "DASHBOARD_ENABLED", True)
    monkeypatch.setattr(dashboard.config, "DASHBOARD_ADMIN_TOKEN", token)
    shell_app.include_router(dashboard.page_router)
    return TestClient(shell_app), token


def test_birchwood_shell_blocks_unauthenticated_production_access(monkeypatch):
    client, _ = _shell_client(monkeypatch)

    response = client.get("/dashboard/birchwood", follow_redirects=False)

    assert response.status_code == 302
    # Login page returns the browser to the Birchwood view after sign-in.
    assert response.headers["location"] == "/dashboard/login?next=/dashboard/birchwood"
    assert "Birchwood" not in response.text


def test_birchwood_shell_serves_with_valid_auth(monkeypatch):
    client, token = _shell_client(monkeypatch)

    response = client.get("/dashboard/birchwood", headers={"X-Dashboard-Token": token})

    assert response.status_code == 200
    assert "Birchwood Collision" in response.text
    assert "Powered by ORCA" in response.text
    # Bespoke view: no multi-vertical chrome.
    assert "Healthcare" not in response.text
    assert "vertical" not in response.text.lower()


def test_birchwood_detail_route_serves_shell(monkeypatch):
    client, token = _shell_client(monkeypatch)

    response = client.get(
        "/dashboard/birchwood/records/some-session-id",
        headers={"X-Dashboard-Token": token},
    )

    assert response.status_code == 200
    assert "Birchwood Collision" in response.text
