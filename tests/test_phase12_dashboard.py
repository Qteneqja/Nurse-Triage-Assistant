from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.admin as admin_api
import src.api.dashboard as dashboard
from src.admin.actions import reset_proposed_action_service
from src.admin.schemas import SessionListItem
from src.api.dashboard_privacy import mask_dashboard_payload, mask_name, mask_phone
from src.orchestrator.schemas import (
    DecisionTraceEntry,
    DispositionCategory,
    FinalizeOutput,
    OrchestratorSession,
)
from src.platform.extraction.schemas import ExtractionResult
from src.platform.workflows.schemas import WorkflowFinalResult


class FakeDashboardRepo:
    def __init__(
        self,
        sessions: list[OrchestratorSession],
        extractions: dict[str, list[Any]] | None = None,
    ) -> None:
        self.sessions = {session.session_id: session for session in sessions}
        self.extractions = extractions or {}

    def list_recent_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        vertical_key: str | None = None,
        workflow_id: str | None = None,
        status: str | None = None,
    ) -> list[OrchestratorSession]:
        sessions = sorted(
            self.sessions.values(),
            key=lambda session: session.created_at,
            reverse=True,
        )
        if vertical_key:
            sessions = [s for s in sessions if s.vertical_key == vertical_key]
        if workflow_id:
            sessions = [s for s in sessions if s.workflow_id == workflow_id]
        if status:
            sessions = [
                s
                for s in sessions
                if ("ended" if s.is_finalized else "active") == status
            ]
        return sessions[offset : offset + limit]

    def load_session(self, session_id: str) -> OrchestratorSession | None:
        return self.sessions.get(session_id)

    def get_session_turns(self, session_id: str) -> list[Any]:
        session = self.sessions.get(session_id)
        return list(session.decision_trace) if session else []

    def get_session_extractions(self, session_id: str) -> list[Any]:
        return list(self.extractions.get(session_id, []))

    def list_organizations(self) -> list[Any]:
        return [
            {
                "organization_id": "org-1",
                "name": "Healthcare Org",
                "slug": "healthcare-org",
                "status": "active",
                "verticals": ["healthcare"],
                "workflows": ["healthcare_triage_v1"],
            },
            {
                "organization_id": "org-2",
                "name": "Property Org",
                "slug": "property-org",
                "status": "active",
                "verticals": ["property_management"],
                "workflows": ["property_management_maintenance_v1"],
            },
        ]


def _client(repo: FakeDashboardRepo, monkeypatch) -> TestClient:
    app = FastAPI()
    monkeypatch.setattr(dashboard, "get_session_repository", lambda: repo)
    monkeypatch.setattr(dashboard.config, "APP_ENV", "test")
    monkeypatch.setattr(dashboard.config, "ENVIRONMENT", "test")
    monkeypatch.setattr(dashboard.config, "DASHBOARD_ENABLED", True)
    monkeypatch.setattr(dashboard.config, "DASHBOARD_ADMIN_TOKEN", "")
    app.include_router(dashboard.api_router, prefix="/api/v1/dashboard")
    return TestClient(app)


def _admin_client(repo: FakeDashboardRepo, monkeypatch) -> TestClient:
    reset_proposed_action_service()
    app = FastAPI()
    monkeypatch.setattr(admin_api, "get_session_repository", lambda: repo)
    monkeypatch.setattr(dashboard.config, "APP_ENV", "test")
    monkeypatch.setattr(dashboard.config, "ENVIRONMENT", "test")
    monkeypatch.setattr(dashboard.config, "DASHBOARD_ENABLED", True)
    monkeypatch.setattr(dashboard.config, "DASHBOARD_ADMIN_TOKEN", "")
    app.include_router(admin_api.router, prefix="/admin")
    app.include_router(dashboard.page_router)
    return TestClient(app)


def _healthcare_session() -> OrchestratorSession:
    session = OrchestratorSession(
        session_id="health-1",
        call_sid="+15551234567",
        organization_id="org-1",
        vertical_key="healthcare",
        workflow_id="healthcare_triage_v1",
        workflow_version="v1",
        phone_number_id="phone-health",
        is_finalized=True,
        finalization_reason="sufficient_information",
        turn_count=7,
    )
    session.intake_state.caller_name = "Jane Doe"
    session.intake_state.caller_age = 44
    session.intake_state.caller_sex = "female"
    session.intake_state.chief_complaint = "abdominal pain"
    session.finalize_output = FinalizeOutput(
        disposition=DispositionCategory.URGENT,
        disposition_reasoning="Abdominal pain needs timely clinical review.",
        sbar_report="S: Abdominal pain\nB: Onset today\nA: Needs review\nR: Nurse review",
        safety_net_instructions=["Call emergency services if symptoms worsen."],
        patient_summary="Abdominal pain reported.",
    )
    session.decision_trace.append(
        DecisionTraceEntry(
            turn_number=1,
            user_text="My name is Jane Doe and my phone is +15551234567.",
            system_response="When did it start?",
            extracted_entities={"chief_complaint": "abdominal pain"},
            red_flags_triggered=["severe_pain_check"],
            rules_triggered=["abdominal_pain_protocol"],
            confidence_score=0.86,
            disposition="URGENT",
            escalation_required=False,
            protocol_citations=["PROTO-003"],
        )
    )
    return session


def _property_session() -> OrchestratorSession:
    session = OrchestratorSession(
        session_id="property-1",
        call_sid="+15557654321",
        organization_id="org-2",
        vertical_key="property_management",
        workflow_id="property_management_maintenance_v1",
        workflow_version="v1",
        phone_number_id="phone-property",
        is_finalized=True,
        turn_count=1,
    )
    session.channel_metadata["scripted_intake"] = {
        "fields": {
            "caller_name": "Jordan Smith",
            "property_address": "123 Main Street",
            "unit_number": "4B",
            "callback_phone": "+15557654321",
            "issue_type": "plumbing",
            "issue_description": "Active flooding under the sink.",
            "access_permission": "yes",
        }
    }
    final_result = WorkflowFinalResult(
        final_disposition="EMERGENCY",
        confidence_score=0.93,
        summary="Active flooding requires emergency maintenance response.",
        structured_output={
            "work_order": {
                "caller_name": "Jordan Smith",
                "property_address": "123 Main Street",
                "unit_number": "4B",
                "issue_type": "plumbing",
                "issue_description": "Active flooding under the sink.",
                "access_permission": "yes",
                "callback_phone": "+15557654321",
                "disposition": "EMERGENCY",
                "recommended_action": "Dispatch emergency plumbing vendor.",
                "vendor_type": "plumbing",
                "safety_flags": ["active_flooding"],
            }
        },
        safety_events=[{"flag": "active_flooding"}],
        rules_triggered=["property:active_flooding"],
    )
    session.channel_metadata["workflow_final_result"] = final_result.model_dump(
        mode="json"
    )
    session.decision_trace.append(
        DecisionTraceEntry(
            turn_number=1,
            user_text="I am Jordan Smith at 123 Main Street. Call me at +15557654321.",
            system_response="I will create an emergency work order.",
            confidence_score=0.93,
            disposition="EMERGENCY",
            escalation_required=True,
            rules_triggered=["property:active_flooding"],
        )
    )
    return session


def _property_extraction() -> ExtractionResult:
    return ExtractionResult(
        extraction_id="extract-1",
        session_id="property-1",
        organization_id="org-2",
        vertical="property_management",
        workflow_id="property_management_maintenance_v1",
        schema_version="property_management_extraction_v1",
        summary="Tenant reports active flooding.",
        entities={"callback_phone": "+15557654321", "caller_name": "Jordan Smith"},
        metrics={},
        flags=["active_flooding"],
        recommended_actions=["dispatch_vendor"],
        confidence_score=0.9,
        raw_model_output="raw output should not leave the dashboard API",
        created_at=datetime.now(UTC),
    )


def test_dashboard_summary_handles_empty_database(monkeypatch):
    client = _client(FakeDashboardRepo([]), monkeypatch)

    response = client.get("/api/v1/dashboard/summary")

    assert response.status_code == 200
    assert response.json()["total_calls"] == 0
    assert response.json()["recent_calls"] == []


def test_calls_list_returns_multiple_verticals_and_masks_values(monkeypatch):
    repo = FakeDashboardRepo([_healthcare_session(), _property_session()])
    client = _client(repo, monkeypatch)

    response = client.get("/api/v1/dashboard/calls")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert {call["vertical_key"] for call in payload["calls"]} == {
        "healthcare",
        "property_management",
    }
    assert "+1555" not in response.text
    assert "***-***-" in response.text


def test_calls_list_supports_vertical_filter(monkeypatch):
    repo = FakeDashboardRepo([_healthcare_session(), _property_session()])
    client = _client(repo, monkeypatch)

    response = client.get("/api/v1/dashboard/calls?vertical_key=property_management")

    assert response.status_code == 200
    calls = response.json()["calls"]
    assert len(calls) == 1
    assert calls[0]["session_id"] == "property-1"


def test_session_detail_returns_healthcare_specific_fields(monkeypatch):
    client = _client(FakeDashboardRepo([_healthcare_session()]), monkeypatch)

    response = client.get("/api/v1/dashboard/calls/health-1")

    assert response.status_code == 200
    detail = response.json()
    assert detail["healthcare"]["disposition"] == "URGENT"
    assert detail["healthcare"]["sbar"]
    assert detail["property_management"] is None
    assert detail["red_flags_triggered"] == ["severe_pain_check"]
    assert "Jane Doe" not in response.text
    assert "+15551234567" not in response.text


def test_session_detail_returns_property_specific_fields_and_extraction(monkeypatch):
    repo = FakeDashboardRepo(
        [_property_session()],
        extractions={"property-1": [_property_extraction()]},
    )
    client = _client(repo, monkeypatch)

    response = client.get("/api/v1/dashboard/calls/property-1")

    assert response.status_code == 200
    detail = response.json()
    assert detail["property_management"]["urgency"] == "EMERGENCY"
    assert detail["property_management"]["vendor_type"] == "plumbing"
    assert detail["extraction"]["extraction_id"] == "extract-1"
    assert "raw output" not in response.text
    assert "+15557654321" not in response.text
    assert "Jordan Smith" not in response.text


def test_session_detail_missing_session_returns_404(monkeypatch):
    client = _client(FakeDashboardRepo([]), monkeypatch)

    response = client.get("/api/v1/dashboard/calls/missing")

    assert response.status_code == 404


def test_extraction_endpoint_returns_safe_empty_response_when_missing(monkeypatch):
    client = _client(FakeDashboardRepo([_healthcare_session()]), monkeypatch)

    response = client.get("/api/v1/dashboard/calls/health-1/extraction")

    assert response.status_code == 200
    assert response.json()["has_extraction"] is False
    assert response.json()["latest_extraction"] is None


def test_actions_endpoint_is_openclaw_placeholder(monkeypatch):
    client = _client(FakeDashboardRepo([]), monkeypatch)

    response = client.get("/api/v1/dashboard/actions")

    assert response.status_code == 200
    assert response.json()["actions"] == []
    assert response.json()["openclaw_integrated"] is False
    assert "proposed" in response.json()["statuses"]


def test_dashboard_privacy_helpers_mask_phi():
    payload = {
        "caller_name": "Jane Doe",
        "callback_phone": "+15551234567",
        "transcript": [
            {"role": "caller", "text": "My name is Jane Doe at +15551234567"}
        ],
    }

    masked = mask_dashboard_payload(payload)

    assert mask_phone("+15551234567") == "***-***-4567"
    assert mask_name("Jane Doe") == "J. D."
    assert masked["caller_name"] == "J. D."
    assert masked["callback_phone"] == "***-***-4567"
    assert "Jane Doe" not in str(masked)
    assert "+15551234567" not in str(masked)


def test_dashboard_api_requires_token_in_production_like_env(monkeypatch):
    repo = FakeDashboardRepo([])
    app = FastAPI()
    monkeypatch.setattr(dashboard, "get_session_repository", lambda: repo)
    monkeypatch.setattr(dashboard.config, "APP_ENV", "production")
    monkeypatch.setattr(dashboard.config, "ENVIRONMENT", "production")
    monkeypatch.setattr(dashboard.config, "DASHBOARD_ENABLED", True)
    monkeypatch.setattr(dashboard.config, "DASHBOARD_ADMIN_TOKEN", "secret")
    app.include_router(dashboard.api_router, prefix="/api/v1/dashboard")
    client = TestClient(app)

    denied = client.get("/api/v1/dashboard/summary")
    allowed = client.get(
        "/api/v1/dashboard/summary",
        headers={"X-Dashboard-Token": "secret"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_admin_dashboard_shell_and_routes_load(monkeypatch):
    client = _admin_client(
        FakeDashboardRepo([_healthcare_session(), _property_session()]),
        monkeypatch,
    )

    shell = client.get("/dashboard")
    organizations = client.get("/admin/organizations")
    workflows = client.get("/admin/workflows")
    sessions = client.get("/admin/sessions")

    assert shell.status_code == 200
    assert "Voice Decision Support Platform" in shell.text
    assert organizations.status_code == 200
    assert workflows.status_code == 200
    assert sessions.status_code == 200
    assert {row["organization_id"] for row in organizations.json()} >= {
        "org-1",
        "org-2",
    }
    assert {workflow["workflow_id"] for workflow in workflows.json()} >= {
        "healthcare_triage_v1",
        "property_management_maintenance_v1",
    }


def test_admin_sessions_list_schema_serializes(monkeypatch):
    client = _admin_client(
        FakeDashboardRepo([_healthcare_session(), _property_session()]),
        monkeypatch,
    )

    response = client.get("/admin/sessions")

    assert response.status_code == 200
    sessions = [SessionListItem.model_validate(item) for item in response.json()]
    assert {session.vertical for session in sessions} == {
        "healthcare",
        "property_management",
    }
    assert sessions[0].session_id


def test_admin_session_detail_includes_healthcare_blocked_reason(monkeypatch):
    session = _healthcare_session()
    session.channel_metadata["healthcare_intake_completeness"] = {
        "is_complete": False,
        "missing_items": ["severity"],
        "finalization_blocked_reason": "missing_clinical_items",
    }
    session.channel_metadata["healthcare_finalization_blocked_reason"] = (
        "missing_clinical_items"
    )
    client = _admin_client(FakeDashboardRepo([session]), monkeypatch)

    response = client.get("/admin/sessions/health-1")

    assert response.status_code == 200
    detail = response.json()
    assert (
        detail["audit_metadata"]["healthcare_finalization_blocked_reason"]
        == "missing_clinical_items"
    )
    assert (
        detail["healthcare_metadata"]["healthcare_finalization_blocked_reason"]
        == "missing_clinical_items"
    )
    assert detail["healthcare_metadata"]["sbar_available"] is True


def test_admin_session_detail_includes_property_work_order_metadata(monkeypatch):
    client = _admin_client(FakeDashboardRepo([_property_session()]), monkeypatch)

    response = client.get("/admin/sessions/property-1")

    assert response.status_code == 200
    metadata = response.json()["property_management_metadata"]
    assert metadata["work_order_output"]["disposition"] == "EMERGENCY"
    assert metadata["property_address"] == "[REDACTED_ADDRESS]"
    assert metadata["unit_number"] == "4B"
    assert metadata["issue_type"] == "plumbing"
    assert metadata["required_fields_completeness"]["is_complete"] is True


def test_admin_proposed_actions_generated_but_not_executed(monkeypatch):
    client = _admin_client(FakeDashboardRepo([_healthcare_session()]), monkeypatch)

    response = client.get("/admin/sessions/health-1/actions")

    assert response.status_code == 200
    actions = response.json()
    assert [action["title"] for action in actions] == [
        "Review SBAR",
        "Call patient back",
        "Escalate to nurse queue",
    ]
    assert all(action["status"] == "proposed" for action in actions)
    assert all(action["execution_attempted"] is False for action in actions)
    assert all(action["external_action_executed"] is False for action in actions)
    assert all(action["source"] == "phase12_placeholder" for action in actions)


def test_admin_action_status_transitions_only_change_status(monkeypatch):
    client = _admin_client(FakeDashboardRepo([_property_session()]), monkeypatch)
    actions = client.get("/admin/sessions/property-1/actions").json()
    action = actions[0]
    action_id = quote(action["action_id"], safe="")

    approved = client.post(
        f"/admin/sessions/property-1/actions/{action_id}/approve"
    ).json()
    rejected = client.post(
        f"/admin/sessions/property-1/actions/{action_id}/reject"
    ).json()
    completed = client.post(
        f"/admin/sessions/property-1/actions/{action_id}/complete"
    ).json()

    assert approved["status"] == "approved"
    assert rejected["status"] == "rejected"
    assert completed["status"] == "completed"
    assert completed["title"] == action["title"]
    assert completed["payload"] == action["payload"]
    assert completed["execution_attempted"] is False
    assert completed["external_action_executed"] is False


def test_admin_action_approval_cannot_change_healthcare_disposition(monkeypatch):
    repo = FakeDashboardRepo([_healthcare_session()])
    client = _admin_client(repo, monkeypatch)
    before = client.get("/admin/sessions/health-1").json()
    action = client.get("/admin/sessions/health-1/actions").json()[0]

    response = client.post(
        "/admin/sessions/health-1/actions/"
        f"{quote(action['action_id'], safe='')}/approve"
    )
    after = client.get("/admin/sessions/health-1").json()

    assert response.status_code == 200
    assert before["session"]["disposition"] == "URGENT"
    assert after["session"]["disposition"] == "URGENT"
    assert repo.load_session("health-1").finalize_output.disposition.value == "URGENT"
