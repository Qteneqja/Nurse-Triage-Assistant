"""PR 4 — Dashboard intake records: list, detail, status workflow, audit.

The intake record is what Birchwood judges the pilot by. These tests lock:
  - injury-flagged and urgent records pin to the top of the list
  - filters: record_status, injury_flagged, urgent_only, date range
  - the one write operation (status change) with an immutable audit trail
  - auth gating in production-like envs
  - contact policy: structured contact shown for non-healthcare records
    (the shop must call back), maskable by flag; healthcare always masked
  - healthcare/insurance render through the same generic record view
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.orchestrator.schemas import OrchestratorSession

BW_WORKFLOW = "birchwood_collision_intake_v1"


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


def _bw_session(
    repo,
    *,
    session_id: str,
    minutes_ago: int,
    flags: list[str] | None = None,
    disposition: str = "COMPLETED_INTAKE",
    human_review: bool = False,
) -> OrchestratorSession:
    session = OrchestratorSession(session_id=session_id, call_sid=f"CA-{session_id}")
    session.vertical_key = "automotive_collision"
    session.workflow_id = BW_WORKFLOW
    session.workflow_version = "v1"
    session.is_finalized = True
    session.created_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    session.channel_metadata["workflow_final_result"] = {
        "final_disposition": disposition,
        "confidence_score": 0.9,
        "summary": "intake",
        "structured_output": {
            "intake_record": {
                "caller_name": "Jordan Sample",
                "phone": "+12045550123",
                "email": "jordan@example.com",
                "vehicle_year": 2021,
                "vehicle_make": "Toyota",
                "vehicle_model": "Corolla",
                "incident_description": (
                    "Rear-ended at a light. Call me at 204-555-0123."
                ),
                "flags": flags or [],
                "missing_information": [],
                "recommended_action": "Call the customer back.",
                "human_review_required": human_review,
                "callback_needed": False,
                "plain_summary": "We recorded your collision details.",
                "shop_summary": "SITUATION: rear-ended.\nRECOMMENDED ACTION: call back.",
            }
        },
        "safety_events": [],
        "rules_triggered": [],
        "audit_metadata": {},
    }
    repo._backend.save_session(session)
    return session


def _healthcare_session(repo, *, session_id: str, minutes_ago: int):
    session = OrchestratorSession(session_id=session_id, call_sid=f"CA-{session_id}")
    session.vertical_key = "healthcare"
    session.workflow_id = "healthcare_triage_v1"
    session.is_finalized = True
    session.created_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    session.intake_state.caller_name = "Pat Patient"
    session.intake_state.chief_complaint = "chest pain"
    repo._backend.save_session(session)
    return session


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_records_list_pins_injury_and_urgent_to_top(client):
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_repo(mp)
        _bw_session(repo, session_id="plain-new", minutes_ago=1)
        _bw_session(
            repo,
            session_id="urgent-transfer",
            minutes_ago=2,
            disposition="TRANSFER_COLLISION_CENTER",
        )
        _bw_session(
            repo,
            session_id="injured-old",
            minutes_ago=60,
            flags=["injuries_reported"],
            human_review=True,
        )

        body = client.get("/api/v1/dashboard/records").json()
        order = [r["session_id"] for r in body["records"]]
        assert order == ["injured-old", "urgent-transfer", "plain-new"]
        injured = body["records"][0]
        assert injured["injury_flagged"] is True
        assert injured["record_status"] == "escalated"  # derived default
        assert injured["status_derived"] is True
        assert body["records"][2]["record_status"] == "new"


def test_records_filters(client):
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_repo(mp)
        _bw_session(repo, session_id="a-new", minutes_ago=1)
        _bw_session(
            repo,
            session_id="b-injury",
            minutes_ago=2,
            flags=["injuries_reported"],
        )
        _bw_session(
            repo,
            session_id="c-old",
            minutes_ago=60 * 24 * 3,
        )

        records = client.get(
            "/api/v1/dashboard/records", params={"injury_flagged": "true"}
        ).json()["records"]
        assert [r["session_id"] for r in records] == ["b-injury"]

        records = client.get(
            "/api/v1/dashboard/records", params={"urgent_only": "true"}
        ).json()["records"]
        assert [r["session_id"] for r in records] == ["b-injury"]

        records = client.get(
            "/api/v1/dashboard/records", params={"record_status": "new"}
        ).json()["records"]
        assert {r["session_id"] for r in records} == {"a-new", "c-old"}

        cutoff = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        records = client.get(
            "/api/v1/dashboard/records", params={"date_from": cutoff}
        ).json()["records"]
        assert {r["session_id"] for r in records} == {"a-new", "b-injury"}

        response = client.get(
            "/api/v1/dashboard/records", params={"record_status": "bogus"}
        )
        assert response.status_code == 422


def test_status_workflow_and_audit_trail(client):
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_repo(mp)
        _bw_session(repo, session_id="rec-1", minutes_ago=5)

        response = client.post(
            "/api/v1/dashboard/records/rec-1/status",
            json={"status": "contacted", "actor": "shop.front-desk"},
        )
        assert response.status_code == 200
        event = response.json()["event"]
        assert event["status"] == "contacted"
        assert event["actor"] == "shop.front-desk"
        assert event["created_at"]

        client.post(
            "/api/v1/dashboard/records/rec-1/status",
            json={
                "status": "scheduled",
                "actor": "shop.front-desk",
                "note": "Booked for Thursday",
            },
        )

        detail = client.get("/api/v1/dashboard/records/rec-1").json()
        assert detail["record_status"] == "scheduled"
        assert detail["status_derived"] is False
        history = detail["status_history"]
        assert [e["status"] for e in history] == ["scheduled", "contacted"]
        assert history[0]["note"] == "Booked for Thursday"

        # The list reflects the explicit status too.
        records = client.get(
            "/api/v1/dashboard/records", params={"record_status": "scheduled"}
        ).json()["records"]
        assert [r["session_id"] for r in records] == ["rec-1"]


def test_status_update_validation_and_missing_record(client):
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_repo(mp)
        _bw_session(repo, session_id="rec-2", minutes_ago=5)

        assert (
            client.post(
                "/api/v1/dashboard/records/rec-2/status",
                json={"status": "shipped", "actor": "x"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/dashboard/records/rec-2/status",
                json={"status": "contacted", "actor": ""},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/dashboard/records/nope/status",
                json={"status": "contacted", "actor": "x"},
            ).status_code
            == 404
        )


def test_contact_policy_birchwood_vs_healthcare(client):
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_repo(mp)
        _bw_session(repo, session_id="rec-bw", minutes_ago=1)
        _healthcare_session(repo, session_id="rec-hc", minutes_ago=2)

        detail = client.get("/api/v1/dashboard/records/rec-bw").json()
        record = detail["intake_record"]
        # The shop can actually call the customer back…
        assert record["phone"] == "+12045550123"
        assert record["caller_name"] == "Jordan Sample"
        # …but free text still masks stray digits.
        assert "204-555-0123" not in (detail["narrative"] or "")
        assert detail["dashboard_display_fields"]  # PR 3 spec contract
        assert detail["shop_summary"].startswith("SITUATION:")

        # Healthcare renders through the same generic view, fully masked.
        hc = client.get("/api/v1/dashboard/records/rec-hc").json()
        assert hc["record"]["vertical_key"] == "healthcare"
        contact = hc["record"]["contact"]
        assert contact["phone"] != "+12045550123"
        assert "Pat Patient" not in str(hc["record"])

        with pytest.MonkeyPatch.context() as inner:
            inner.setattr("src.config.DASHBOARD_RECORDS_SHOW_CONTACT", False)
            masked = client.get("/api/v1/dashboard/records/rec-bw").json()
            assert masked["intake_record"]["phone"] != "+12045550123"


def test_records_api_requires_token_in_staging(client):
    with pytest.MonkeyPatch.context() as mp:
        repo = _setup_repo(mp)
        _bw_session(repo, session_id="rec-auth", minutes_ago=1)
        mp.setattr("src.config.APP_ENV", "staging")
        mp.setattr("src.config.DASHBOARD_ADMIN_TOKEN", "token-1234567890")

        assert client.get("/api/v1/dashboard/records").status_code == 401
        ok = client.get(
            "/api/v1/dashboard/records",
            headers={"X-Dashboard-Token": "token-1234567890"},
        )
        assert ok.status_code == 200
        assert (
            client.post(
                "/api/v1/dashboard/records/rec-auth/status",
                json={"status": "contacted", "actor": "x"},
            ).status_code
            == 401
        )


def test_dashboard_shell_serves_records_pages(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup_repo(mp)
        page = client.get("/dashboard/records")
        assert page.status_code == 200
        assert "Records" in page.text
        detail_page = client.get("/dashboard/records/some-session-id")
        assert detail_page.status_code == 200


def test_record_status_event_model_round_trip():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from src.storage.models import Base, RecordStatusEventModel

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            RecordStatusEventModel(
                session_id="sess-1",
                status="contacted",
                actor="shop.front-desk",
                note="left voicemail",
            )
        )
        db.commit()
        row = db.execute(select(RecordStatusEventModel)).scalar_one()
        assert row.status == "contacted"
        assert row.actor == "shop.front-desk"
        assert row.created_at is not None


def test_dashboard_static_pages_are_csp_compatible():
    """The app serves script-src 'self' / style-src 'self' — inline <script>
    or <style> blocks silently never run in the browser (PR 4.2 regression:
    the login page's inline JS made 'Sign in' do nothing)."""
    from pathlib import Path

    static_dir = Path("src/dashboard_static")
    for page in static_dir.glob("*.html"):
        html = page.read_text(encoding="utf-8").lower()
        assert "<script>" not in html, f"{page.name}: inline <script> is CSP-blocked"
        assert "<style>" not in html, f"{page.name}: inline <style> is CSP-blocked"
        # Event handlers as attributes are inline script too.
        assert "onclick=" not in html, f"{page.name}: inline handler is CSP-blocked"


def test_login_page_uses_external_assets(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup_repo(mp)
        mp.setattr("src.config.APP_ENV", "staging")
        mp.setattr("src.config.DASHBOARD_ADMIN_TOKEN", "token-1234567890")
        page = client.get("/dashboard/login")
        assert page.status_code == 200
        assert "/dashboard/static/login.js" in page.text
