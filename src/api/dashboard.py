"""Dashboard/admin API and static shell routes for Phase 12 + PR 4 records."""

from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

import src.config as config
from src.api.dashboard_privacy import mask_dashboard_payload, safe_display_value
from src.orchestrator.schemas import OrchestratorSession
from src.storage.session_repository import get_session_repository

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parents[1] / "dashboard_static"
_INDEX_FILE = _STATIC_DIR / "index.html"
_LOGIN_FILE = _STATIC_DIR / "login.html"
_OPENCLAW_STATUSES = [
    "proposed",
    "approved",
    "rejected",
    "edited",
    "completed",
    "failed",
]


class ProposedAction(BaseModel):
    """Future OpenClaw action approval record.

    Phase 12 exposes the contract only. No OpenClaw integration is performed.
    """

    action_id: str
    session_id: str | None = None
    organization_id: str | None = None
    vertical_key: str | None = None
    workflow_id: str | None = None
    action_type: str
    title: str
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal[
        "proposed",
        "approved",
        "rejected",
        "edited",
        "completed",
        "failed",
    ] = "proposed"
    source: Literal["manual", "system", "openclaw_placeholder"] = "openclaw_placeholder"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None


def require_dashboard_api_access(
    authorization: str | None = Header(default=None),
    x_dashboard_token: str | None = Header(default=None),
) -> None:
    """Protect dashboard API in staging/production without blocking local dev."""
    if not config.DASHBOARD_ENABLED:
        raise HTTPException(status_code=404, detail="Dashboard is disabled")

    env = (config.APP_ENV or config.ENVIRONMENT).lower()
    if env in {"development", "test"}:
        return

    expected = config.DASHBOARD_ADMIN_TOKEN
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Dashboard admin token is not configured",
        )

    token = x_dashboard_token or _bearer_token(authorization)
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Dashboard token required")


def require_dashboard_shell_enabled() -> None:
    if not config.DASHBOARD_ENABLED:
        raise HTTPException(status_code=404, detail="Dashboard is disabled")


def require_dashboard_shell_access(
    authorization: str | None = Header(default=None),
    x_dashboard_token: str | None = Header(default=None),
) -> None:
    """Protect the browser dashboard shell with the same production auth gate."""

    require_dashboard_api_access(
        authorization=authorization,
        x_dashboard_token=x_dashboard_token,
    )


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    return None


api_router = APIRouter(dependencies=[Depends(require_dashboard_api_access)])
page_router = APIRouter()


@api_router.get("/summary")
async def dashboard_summary() -> dict[str, Any]:
    repo = get_session_repository()
    sessions = repo.list_recent_sessions(limit=1000)
    calls = [_call_row(repo, session) for session in sessions]

    by_vertical: dict[str, int] = {}
    by_workflow: dict[str, int] = {}
    urgent_healthcare = 0
    urgent_property = 0
    extraction_count = 0

    for call in calls:
        vertical = call["vertical_key"] or "unknown"
        workflow = call["workflow_id"] or "unknown"
        disposition = (call["disposition_or_urgency"] or "").upper()
        by_vertical[vertical] = by_vertical.get(vertical, 0) + 1
        by_workflow[workflow] = by_workflow.get(workflow, 0) + 1
        if vertical == "healthcare" and disposition in {"ER_NOW", "URGENT"}:
            urgent_healthcare += 1
        if vertical == "property_management" and disposition in {
            "EMERGENCY",
            "SAME_DAY",
        }:
            urgent_property += 1
        if call["has_extraction"]:
            extraction_count += 1

    return {
        "total_calls": len(calls),
        "calls_by_vertical": by_vertical,
        "calls_by_workflow": by_workflow,
        "healthcare_urgent_or_er_count": urgent_healthcare,
        "property_emergency_or_same_day_count": urgent_property,
        "urgent_or_emergency_count": urgent_healthcare + urgent_property,
        "extraction_count": extraction_count,
        "pending_actions_count": 0,
        "recent_calls": calls[:10],
    }


@api_router.get("/calls")
async def list_dashboard_calls(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    vertical_key: str | None = None,
    workflow_id: str | None = None,
    status: str | None = None,
    disposition: str | None = None,
) -> dict[str, Any]:
    repo = get_session_repository()
    sessions = repo.list_recent_sessions(
        limit=limit,
        offset=offset,
        vertical_key=vertical_key,
        workflow_id=workflow_id,
        status=status,
    )
    calls = [_call_row(repo, session) for session in sessions]
    if disposition:
        wanted = disposition.upper()
        calls = [
            call
            for call in calls
            if (call["disposition_or_urgency"] or "").upper() == wanted
        ]
    return {"count": len(calls), "limit": limit, "offset": offset, "calls": calls}


@api_router.get("/calls/{session_id}")
async def get_dashboard_call(session_id: str) -> dict[str, Any]:
    repo = get_session_repository()
    session = repo.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_detail(repo, session)


@api_router.get("/calls/{session_id}/turns")
async def get_dashboard_call_turns(session_id: str) -> dict[str, Any]:
    repo = get_session_repository()
    session = repo.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    turns = _safe_turns(repo, session)
    return {"session_id": session_id, "count": len(turns), "turns": turns}


@api_router.get("/calls/{session_id}/final-result")
async def get_dashboard_call_final_result(session_id: str) -> dict[str, Any]:
    repo = get_session_repository()
    session = repo.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "final_result": mask_dashboard_payload(_final_result(session)),
    }


@api_router.get("/calls/{session_id}/extraction")
async def get_dashboard_call_extraction(session_id: str) -> dict[str, Any]:
    repo = get_session_repository()
    session = repo.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    extractions = [_serialize_extraction(item) for item in _extractions(repo, session)]
    return {
        "session_id": session_id,
        "has_extraction": bool(extractions),
        "count": len(extractions),
        "extractions": mask_dashboard_payload(extractions),
        "latest_extraction": mask_dashboard_payload(extractions[0])
        if extractions
        else None,
    }


# ---------------------------------------------------------------------------
# Intake records (PR 4) — what the shop actually works from
# ---------------------------------------------------------------------------

RECORD_STATUSES = ["new", "contacted", "scheduled", "completed", "escalated"]

_URGENT_DISPOSITIONS = {
    "ER_NOW",
    "URGENT",
    "EMERGENCY",
    "EMERGENCY_SERVICES_NOW",
    "URGENT_ADJUSTER_REVIEW",
    "TRANSFER_COLLISION_CENTER",
    "SAME_DAY",
}

_CONTACT_KEYS = ("caller_name", "phone", "email", "address")


class RecordStatusUpdate(BaseModel):
    """One write operation: a status change, audited with actor + timestamp."""

    status: Literal["new", "contacted", "scheduled", "completed", "escalated"]
    actor: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


def _records_show_contact() -> bool:
    return bool(getattr(config, "DASHBOARD_RECORDS_SHOW_CONTACT", True))


def _intake_record_dict(session: OrchestratorSession) -> dict[str, Any]:
    final_result = _final_result(session) or {}
    structured = final_result.get("structured_output") or {}
    record = structured.get("intake_record")
    return record if isinstance(record, dict) else {}


def _display_fields_for_workflow(workflow_id: str | None) -> list[str]:
    if not workflow_id:
        return []
    try:
        from src.platform.workflows.registry import (
            ensure_default_workflows_registered,
        )

        spec = ensure_default_workflows_registered().get(workflow_id).get_spec()
    except Exception:
        return []
    if spec is None:
        return []
    return list(spec.dashboard_display_fields)


def _record_status(repo: Any, session: OrchestratorSession) -> dict[str, Any]:
    try:
        events = repo.get_record_status_events(session.session_id)
    except Exception:
        events = []
    injury_flagged, urgent = _record_prominence(session)
    if events:
        current = events[0]["status"]
    else:
        current = "escalated" if (injury_flagged or urgent) else "new"
    return {
        "status": current,
        "status_derived": not events,
        "history": events,
    }


def _record_prominence(session: OrchestratorSession) -> tuple[bool, bool]:
    """(injury_flagged, urgent) — these records pin to the top of the list."""
    record = _intake_record_dict(session)
    flags = [str(f) for f in (record.get("flags") or [])]
    injury_flagged = "injuries_reported" in flags
    disposition = (_disposition(session, _final_result(session)) or "").upper()
    urgent = disposition in _URGENT_DISPOSITIONS
    return injury_flagged, urgent


def _record_row(repo: Any, session: OrchestratorSession) -> dict[str, Any]:
    final_result = _final_result(session) or {}
    record = _intake_record_dict(session)
    injury_flagged, urgent = _record_prominence(session)
    status_info = _record_status(repo, session)
    vertical = _vertical_key(session)
    show_contact = _records_show_contact() and vertical != "healthcare"

    vehicle = " ".join(
        str(part)
        for part in (
            record.get("vehicle_year"),
            record.get("vehicle_make"),
            record.get("vehicle_model"),
        )
        if part
    )
    contact = {
        "caller_name": record.get("caller_name"),
        "phone": record.get("phone"),
    }
    if not show_contact:
        contact = mask_dashboard_payload(contact)

    return {
        "session_id": session.session_id,
        "created_at": _isoformat(session.created_at),
        "vertical_key": vertical,
        "workflow_id": session.workflow_id,
        "record_status": status_info["status"],
        "status_derived": status_info["status_derived"],
        "injury_flagged": injury_flagged,
        "urgent": urgent,
        "urgency_rank": (2 if injury_flagged else 0) + (1 if urgent else 0),
        "disposition": _disposition(session, final_result),
        "vehicle": vehicle or None,
        "contact": contact,
        "summary": _summary_text(session, final_result),
        "flags": [str(f) for f in (record.get("flags") or [])],
        "missing_information": record.get("missing_information") or [],
        "recommended_action": record.get("recommended_action")
        or _recommended_action_from_spec(session, final_result),
        "human_review_required": bool(record.get("human_review_required")),
        "callback_needed": bool(record.get("callback_needed")),
        "is_finalized": session.is_finalized,
    }


def _recommended_action_from_spec(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> str | None:
    disposition = _disposition(session, final_result)
    if not disposition or not session.workflow_id:
        return None
    try:
        from src.platform.workflows.registry import (
            ensure_default_workflows_registered,
        )

        spec = ensure_default_workflows_registered().get(session.workflow_id).get_spec()
    except Exception:
        return None
    if spec is None:
        return None
    return spec.recommended_actions.get(str(disposition))


def _record_payload(session: OrchestratorSession) -> dict[str, Any]:
    """Full intake record for the detail view.

    PII policy: free text is always masked (mask_phi catches stray digits in
    narratives), but for non-healthcare verticals the structured CONTACT
    fields are restored so staff can actually call the customer back — that
    is the dashboard's purpose, behind the auth gate. Healthcare records
    remain fully masked exactly as in Phase 12.
    """
    record = _intake_record_dict(session)
    vertical = _vertical_key(session)
    masked = mask_dashboard_payload(dict(record))
    if _records_show_contact() and vertical != "healthcare":
        for key in _CONTACT_KEYS:
            if key in record:
                masked[key] = record[key]
        customer = record.get("customer")
        if isinstance(customer, dict):
            masked["customer"] = dict(customer)
        for key in ("plain_summary", "shop_summary"):
            if record.get(key):
                masked[key] = record[key]
    return masked


def _parse_date(value: str | None, *, field: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be an ISO date/datetime",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _created_at_utc(session: OrchestratorSession) -> datetime | None:
    value = session.created_at
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@api_router.get("/records")
async def list_intake_records(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    vertical_key: str | None = None,
    workflow_id: str | None = None,
    record_status: str | None = Query(default=None),
    injury_flagged: bool | None = Query(default=None),
    urgent_only: bool = Query(default=False),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
) -> dict[str, Any]:
    if record_status is not None and record_status not in RECORD_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"record_status must be one of {RECORD_STATUSES}",
        )
    from_dt = _parse_date(date_from, field="date_from")
    to_dt = _parse_date(date_to, field="date_to")

    repo = get_session_repository()
    sessions = repo.list_recent_sessions(
        limit=500,
        vertical_key=vertical_key,
        workflow_id=workflow_id,
    )
    rows = [_record_row(repo, session) for session in sessions]

    if record_status is not None:
        rows = [r for r in rows if r["record_status"] == record_status]
    if injury_flagged is not None:
        rows = [r for r in rows if r["injury_flagged"] is injury_flagged]
    if urgent_only:
        rows = [r for r in rows if r["urgent"] or r["injury_flagged"]]
    if from_dt or to_dt:
        by_id = {s.session_id: s for s in sessions}
        filtered = []
        for row in rows:
            created = _created_at_utc(by_id[row["session_id"]])
            if created is None:
                continue
            if from_dt and created < from_dt:
                continue
            if to_dt and created > to_dt:
                continue
            filtered.append(row)
        rows = filtered

    # Injury-flagged and urgent records pin to the top, newest first within
    # each band.
    rows.sort(
        key=lambda r: (r["urgency_rank"], r["created_at"] or ""),
        reverse=True,
    )
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "count": len(page),
        "total_matched": total,
        "limit": limit,
        "offset": offset,
        "statuses": RECORD_STATUSES,
        "records": page,
    }


@api_router.get("/records/{session_id}")
async def get_intake_record(session_id: str) -> dict[str, Any]:
    repo = get_session_repository()
    session = repo.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Record not found")
    row = _record_row(repo, session)
    status_info = _record_status(repo, session)
    record = _intake_record_dict(session)
    payload = _record_payload(session)
    display_fields = _display_fields_for_workflow(session.workflow_id)
    return {
        "record": row,
        "intake_record": payload,
        "dashboard_display_fields": display_fields,
        "record_status": status_info["status"],
        "status_derived": status_info["status_derived"],
        "status_history": status_info["history"],
        "statuses": RECORD_STATUSES,
        "shop_summary": payload.get("shop_summary"),
        "plain_summary": payload.get("plain_summary"),
        "narrative": mask_dashboard_payload(
            record.get("incident_description")
            or record.get("fields", {}).get("incident_description")
        ),
        "turns": _safe_turns(repo, session),
        "rules_triggered": _rules_triggered(session, _final_result(session)),
        "safety_events": mask_dashboard_payload(
            _safety_events(session, _final_result(session))
        ),
        "audit": {
            "finalization_reason": session.finalization_reason,
            "decision_trace_count": len(session.decision_trace),
            "turn_count": session.turn_count,
        },
    }


@api_router.post("/records/{session_id}/status")
async def update_intake_record_status(
    session_id: str,
    update: RecordStatusUpdate,
) -> dict[str, Any]:
    repo = get_session_repository()
    session = repo.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Record not found")
    try:
        event = repo.append_record_status_event(
            session_id=session_id,
            status=update.status,
            actor=update.actor.strip(),
            note=update.note,
        )
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail="Storage backend does not support record status events",
        ) from exc
    # Audit log line — status/actor/session only, never caller PII.
    logger.info(
        "[DASHBOARD] Record status change session=%s status=%s actor=%s",
        session_id,
        update.status,
        update.actor.strip(),
    )
    return {
        "session_id": session_id,
        "record_status": update.status,
        "event": event,
    }


@api_router.get("/actions")
async def list_dashboard_actions() -> dict[str, Any]:
    actions: list[ProposedAction] = []
    return {
        "count": 0,
        "actions": [action.model_dump(mode="json") for action in actions],
        "statuses": _OPENCLAW_STATUSES,
        "openclaw_integrated": False,
    }


@page_router.get("/dashboard/login", include_in_schema=False)
async def dashboard_login(
    _: None = Depends(require_dashboard_shell_enabled),
) -> FileResponse:
    """Unauthenticated sign-in page (static, contains no data).

    A browser cannot send the token header on its initial page load, so the
    shell gate redirects here; this page validates the token against a data
    endpoint, stores it for the JS API helpers, and sets the shell cookie.
    """
    return FileResponse(_LOGIN_FILE)


@page_router.get("/dashboard", include_in_schema=False)
@page_router.get("/dashboard/calls", include_in_schema=False)
@page_router.get("/dashboard/calls/{session_id}", include_in_schema=False)
@page_router.get("/dashboard/sessions", include_in_schema=False)
@page_router.get("/dashboard/sessions/{session_id}", include_in_schema=False)
@page_router.get("/dashboard/records", include_in_schema=False)
@page_router.get("/dashboard/records/{session_id}", include_in_schema=False)
@page_router.get("/dashboard/enrichment", include_in_schema=False)
@page_router.get("/dashboard/actions", include_in_schema=False)
async def dashboard_shell(request: Request):
    """Serve the dashboard shell; browsers without a token go to /login.

    Accepts the admin token via header (curl/tools) OR the dashboard_token
    cookie set by the login page (browsers). Data endpoints remain
    header-authenticated and are unaffected by the cookie.
    """
    require_dashboard_shell_enabled()
    env = (config.APP_ENV or config.ENVIRONMENT).lower()
    if env in {"development", "test"}:
        return FileResponse(_INDEX_FILE)

    expected = config.DASHBOARD_ADMIN_TOKEN
    token = (
        request.headers.get("x-dashboard-token")
        or _bearer_token(request.headers.get("authorization"))
        or request.cookies.get("dashboard_token")
    )
    if expected and token and hmac.compare_digest(token, expected):
        return FileResponse(_INDEX_FILE)
    return RedirectResponse(url="/dashboard/login", status_code=302)


def _call_row(repo: Any, session: OrchestratorSession) -> dict[str, Any]:
    final_result = _final_result(session) or {}
    extractions = _extractions(repo, session)
    turns = _safe_turns(repo, session, include_text=False)
    disposition = _disposition(session, final_result)
    confidence = _confidence(session, final_result)
    dashboard_record = session.channel_metadata.get("_dashboard_record", {})

    return {
        "session_id": session.session_id,
        "created_at": dashboard_record.get("created_at")
        or _isoformat(session.created_at),
        "updated_at": dashboard_record.get("updated_at"),
        "completed_at": dashboard_record.get("ended_at")
        or dashboard_record.get("finalized_at"),
        "vertical_key": _vertical_key(session),
        "workflow_id": session.workflow_id,
        "workflow_version": session.workflow_version,
        "organization_id": session.organization_id,
        "phone_number_id": session.phone_number_id,
        "caller_display": safe_display_value(session.call_sid, "caller_id"),
        "summary": _summary_text(session, final_result),
        "disposition_or_urgency": disposition,
        "confidence_score": confidence,
        "status": dashboard_record.get("status") or _status(session),
        "has_extraction": bool(extractions),
        "has_transcript": bool(turns),
        "safety_flags_count": _safety_flags_count(session, final_result),
    }


def _session_detail(repo: Any, session: OrchestratorSession) -> dict[str, Any]:
    final_result = _final_result(session)
    turns = _safe_turns(repo, session)
    extractions = [_serialize_extraction(item) for item in _extractions(repo, session)]
    scripted_fields = session.channel_metadata.get("scripted_intake", {}).get(
        "fields", {}
    )
    audit_metadata = _audit_metadata(session, final_result)

    detail = {
        "session": _call_row(repo, session),
        "scripted_intake_fields": mask_dashboard_payload(scripted_fields),
        "dynamic_state_summary": mask_dashboard_payload(
            session.intake_state.model_dump(mode="json")
        ),
        "final_result": mask_dashboard_payload(final_result),
        "disposition_or_urgency": _disposition(session, final_result or {}),
        "confidence_score": _confidence(session, final_result or {}),
        "rules_triggered": _rules_triggered(session, final_result),
        "red_flags_triggered": _red_flags(session),
        "safety_events": mask_dashboard_payload(_safety_events(session, final_result)),
        "audit_metadata": mask_dashboard_payload(audit_metadata),
        "extraction": mask_dashboard_payload(extractions[0]) if extractions else None,
        "extractions": mask_dashboard_payload(extractions),
        "turns": turns,
        "transcript": turns,
        "healthcare": None,
        "property_management": None,
        "automotive_collision": None,
    }
    if _vertical_key(session) == "healthcare":
        detail["healthcare"] = _healthcare_view(session, final_result)
    elif _vertical_key(session) == "property_management":
        detail["property_management"] = _property_view(session, final_result)
    elif _vertical_key(session) == "automotive_collision":
        detail["automotive_collision"] = _automotive_collision_view(
            session,
            final_result,
        )
    return detail


def _final_result(session: OrchestratorSession) -> dict[str, Any] | None:
    if session.finalize_output is not None:
        return session.finalize_output.model_dump(mode="json")
    result = session.channel_metadata.get("workflow_final_result")
    if isinstance(result, dict):
        return result
    return None


def _summary_text(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> str | None:
    final_result = final_result or {}
    structured = final_result.get("structured_output") or {}
    work_order = structured.get("work_order") or {}
    collision_record = structured.get("intake_record") or {}
    if work_order.get("issue_description"):
        return safe_display_value(work_order["issue_description"], "summary")
    if collision_record.get("incident_description"):
        return safe_display_value(
            collision_record["incident_description"],
            "summary",
        )
    if session.intake_state.chief_complaint:
        return safe_display_value(session.intake_state.chief_complaint, "summary")
    intake = structured.get("intake_state") or {}
    if intake.get("chief_complaint"):
        return safe_display_value(intake["chief_complaint"], "summary")
    if final_result.get("summary"):
        return safe_display_value(final_result["summary"], "summary")
    return None


def _vertical_key(session: OrchestratorSession) -> str | None:
    if session.vertical_key:
        return session.vertical_key
    workflow_id = session.workflow_id or ""
    if workflow_id.startswith("property_management"):
        return "property_management"
    if workflow_id.startswith("insurance"):
        return "insurance"
    if workflow_id.startswith("birchwood_collision"):
        return "automotive_collision"
    if workflow_id.startswith("healthcare"):
        return "healthcare"
    return None


def _status(session: OrchestratorSession) -> str:
    if session.is_finalized:
        return "ended"
    return "active"


def _disposition(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> str | None:
    final_result = final_result or {}
    if final_result.get("final_disposition"):
        return str(final_result["final_disposition"])
    if final_result.get("disposition"):
        return str(final_result["disposition"])
    if session.finalize_output is not None:
        return session.finalize_output.disposition.value
    if session.decision_trace:
        return session.decision_trace[-1].disposition
    return None


def _confidence(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> float | None:
    final_result = final_result or {}
    value = final_result.get("confidence_score")
    if value is not None:
        return float(value)
    if session.decision_trace:
        return session.decision_trace[-1].confidence_score
    return None


def _safety_flags_count(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> int:
    return len(_safety_events(session, final_result)) + len(_red_flags(session))


def _rules_triggered(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> list[Any]:
    rules: list[Any] = []
    for entry in session.decision_trace:
        rules.extend(entry.rules_triggered)
    if final_result:
        rules.extend(final_result.get("rules_triggered") or [])
    return _dedupe(rules)


def _red_flags(session: OrchestratorSession) -> list[Any]:
    flags: list[Any] = []
    for entry in session.decision_trace:
        flags.extend(entry.red_flags_triggered)
    return _dedupe(flags)


def _safety_events(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> list[Any]:
    events: list[Any] = [flag.model_dump(mode="json") for flag in session.safety_flags]
    if final_result:
        events.extend(final_result.get("safety_events") or [])
        events.extend(final_result.get("llm_safety_flags") or [])
    return events


def _audit_metadata(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = {
        "finalization_reason": session.finalization_reason,
        "turn_count": session.turn_count,
        "max_turns": session.max_turns,
        "audit_trace": session.audit_trace.model_dump(mode="json")
        if session.audit_trace
        else None,
        "decision_trace_count": len(session.decision_trace),
        "channel_metadata": session.channel_metadata,
    }
    if final_result:
        metadata["workflow_audit_metadata"] = final_result.get("audit_metadata") or {}
    return metadata


def _healthcare_view(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> dict[str, Any]:
    final_result = final_result or {}
    structured = final_result.get("structured_output") or {}
    sbar = final_result.get("sbar") or structured.get("sbar")
    if not sbar and final_result.get("sbar_report"):
        sbar = {"report": final_result["sbar_report"]}
    if not sbar and structured.get("sbar_report"):
        sbar = {"report": structured["sbar_report"]}

    protocol_refs: list[Any] = []
    for entry in session.decision_trace:
        protocol_refs.extend(entry.protocol_citations)
        protocol_refs.extend(hit.model_dump(mode="json") for hit in entry.protocol_hits)

    return mask_dashboard_payload(
        {
            "disposition": _disposition(session, final_result),
            "confidence_score": _confidence(session, final_result),
            "sbar": sbar,
            "red_flags": _red_flags(session),
            "rules_triggered": _rules_triggered(session, final_result),
            "protocol_references": _dedupe(protocol_refs),
            "finalization_reason": session.finalization_reason
            or _last_finalization_reason(session),
        }
    )


def _property_view(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> dict[str, Any]:
    final_result = final_result or {}
    structured = final_result.get("structured_output") or {}
    work_order = structured.get("work_order") or {}
    return mask_dashboard_payload(
        {
            "urgency": work_order.get("disposition")
            or final_result.get("final_disposition"),
            "issue_type": work_order.get("issue_type"),
            "issue_description": work_order.get("issue_description"),
            "property_address": work_order.get("property_address"),
            "unit_number": work_order.get("unit_number"),
            "access_permission": work_order.get("access_permission"),
            "callback_phone": work_order.get("callback_phone"),
            "vendor_type": work_order.get("vendor_type"),
            "recommended_action": work_order.get("recommended_action"),
            "safety_flags": work_order.get("safety_flags")
            or final_result.get("safety_events")
            or [],
            "work_order": work_order,
        }
    )


def _automotive_collision_view(
    session: OrchestratorSession,
    final_result: dict[str, Any] | None,
) -> dict[str, Any]:
    final_result = final_result or {}
    structured = final_result.get("structured_output") or {}
    record = structured.get("intake_record") or {}
    return mask_dashboard_payload(
        {
            "workflow_id": session.workflow_id,
            "powered_by": record.get("powered_by") or "ORCA",
            "client_target": record.get("client_target")
            or "Birchwood Automotive Group",
            "workflow_status": record.get("workflow_status") or "demo/pilot",
            "recommended_routing": record.get("recommended_routing")
            or final_result.get("final_disposition"),
            "preferred_collision_center": record.get("preferred_collision_center"),
            "flags": record.get("flags") or [],
            "missing_information": record.get("missing_information") or [],
            "callback_needed": record.get("callback_needed"),
            "intake_record": record,
        }
    )


def _last_finalization_reason(session: OrchestratorSession) -> str | None:
    for entry in reversed(session.decision_trace):
        if entry.finalization_reason:
            return entry.finalization_reason
    return None


def _extractions(repo: Any, session: OrchestratorSession) -> list[Any]:
    try:
        return list(repo.get_session_extractions(session.session_id))
    except AttributeError:
        return []


def _safe_turns(
    repo: Any,
    session: OrchestratorSession,
    include_text: bool = True,
) -> list[dict[str, Any]]:
    try:
        raw_turns = list(repo.get_session_turns(session.session_id))
    except AttributeError:
        raw_turns = []
    if not raw_turns:
        raw_turns = list(session.decision_trace) or list(session.conversation)
    turns = [_serialize_turn(turn, include_text=include_text) for turn in raw_turns]
    return mask_dashboard_payload(turns)


def _serialize_turn(turn: Any, include_text: bool = True) -> dict[str, Any]:
    if hasattr(turn, "turn_index"):
        payload = {
            "turn_index": getattr(turn, "turn_index", None),
            "timestamp": _isoformat(getattr(turn, "timestamp", None)),
            "caller_text": getattr(turn, "user_text", None) if include_text else None,
            "assistant_text": getattr(turn, "system_text", None)
            if include_text
            else None,
            "red_flags_triggered": getattr(turn, "red_flags_triggered", None) or [],
            "rules_triggered": getattr(turn, "rules_triggered", None) or [],
            "protocol_hits": getattr(turn, "protocol_hits", None) or [],
            "protocol_citations": getattr(turn, "protocol_citations", None) or [],
            "confidence_score": getattr(turn, "confidence_score", None),
            "disposition": getattr(turn, "disposition", None),
            "escalation_required": getattr(turn, "escalation_required", None),
        }
    elif hasattr(turn, "turn_number"):
        data = turn.model_dump(mode="json")
        payload = {
            "turn_index": data.get("turn_number"),
            "timestamp": data.get("timestamp"),
            "caller_text": data.get("user_text") if include_text else None,
            "assistant_text": data.get("system_response") if include_text else None,
            "red_flags_triggered": data.get("red_flags_triggered") or [],
            "rules_triggered": data.get("rules_triggered") or [],
            "protocol_hits": data.get("protocol_hits") or [],
            "protocol_citations": data.get("protocol_citations") or [],
            "confidence_score": data.get("confidence_score"),
            "disposition": data.get("disposition"),
            "escalation_required": data.get("escalation_required"),
            "finalization_reason": data.get("finalization_reason"),
        }
    elif hasattr(turn, "model_dump"):
        data = turn.model_dump(mode="json")
        payload = {
            "turn_index": None,
            "timestamp": data.get("timestamp"),
            "role": data.get("role"),
            "text": data.get("text") if include_text else None,
        }
    elif isinstance(turn, dict):
        payload = dict(turn)
        if not include_text:
            payload.pop("text", None)
            payload.pop("caller_text", None)
            payload.pop("assistant_text", None)
            payload.pop("user_text", None)
            payload.pop("system_text", None)
    else:
        payload = {"text": str(turn) if include_text else None}
    return payload


def _serialize_extraction(extraction: Any) -> dict[str, Any]:
    if hasattr(extraction, "model_dump"):
        return extraction.model_dump(mode="json")
    if hasattr(extraction, "entities_json"):
        return {
            "extraction_id": getattr(extraction, "id", None),
            "session_id": getattr(extraction, "session_id", None),
            "organization_id": getattr(extraction, "organization_id", None),
            "vertical": getattr(extraction, "vertical_key", None),
            "workflow_id": getattr(extraction, "workflow_id", None),
            "schema_version": getattr(extraction, "schema_version", None),
            "summary": getattr(extraction, "summary", None),
            "entities": getattr(extraction, "entities_json", None) or {},
            "metrics": getattr(extraction, "metrics_json", None) or {},
            "flags": getattr(extraction, "flags_json", None) or [],
            "recommended_actions": getattr(extraction, "recommended_actions_json", None)
            or [],
            "confidence_score": getattr(extraction, "confidence_score", None),
            "created_at": _isoformat(getattr(extraction, "created_at", None)),
        }
    if isinstance(extraction, dict):
        return extraction
    return {"value": str(extraction)}


def _dedupe(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        key = repr(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _isoformat(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value:
        return str(value)
    return None
