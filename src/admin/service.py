"""Admin/dashboard service layer for multi-vertical read models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.admin.actions import ProposedActionService, get_proposed_action_service
from src.admin.schemas import (
    AuditMetadata,
    DashboardSummary,
    OrganizationListItem,
    ProposedAction,
    SessionDetail,
    SessionListItem,
    TurnDetail,
    WorkflowListItem,
)
from src.api.dashboard_privacy import mask_dashboard_payload
from src.orchestrator.schemas import OrchestratorSession
from src.platform.workflows.registry import ensure_default_workflows_registered
from src.storage.session_repository import SessionRepository


class AdminDashboardService:
    """Build typed admin/dashboard read models from existing repositories."""

    def __init__(
        self,
        session_repository: SessionRepository,
        action_service: ProposedActionService | None = None,
    ) -> None:
        self._sessions = session_repository
        self._actions = action_service or get_proposed_action_service()

    def list_organizations(self) -> list[OrganizationListItem]:
        """Return organizations known to storage or inferred from sessions."""
        rows = self._list_repository_organizations()
        session_counts = _counts_by_organization(self._recent_sessions(limit=1000))

        items: list[OrganizationListItem] = []
        seen: set[str] = set()
        for row in rows:
            item = _organization_item_from_row(row)
            item.session_count = session_counts.get(item.organization_id, 0)
            items.append(item)
            seen.add(item.organization_id)

        for session in self._recent_sessions(limit=1000):
            org_id = session.organization_id
            if not org_id or org_id in seen:
                continue
            items.append(
                OrganizationListItem(
                    organization_id=org_id,
                    name=org_id,
                    verticals=[_vertical_key(session) or "unknown"],
                    workflows=[session.workflow_id or "unknown"],
                    session_count=session_counts.get(org_id, 0),
                )
            )
            seen.add(org_id)

        items.sort(key=lambda item: item.name.lower())
        return items

    def list_workflows(self) -> list[WorkflowListItem]:
        """Return registered workflow definitions with recent session counts."""
        registry = ensure_default_workflows_registered()
        sessions = self._recent_sessions(limit=1000)
        counts: dict[str, int] = {}
        for session in sessions:
            key = session.workflow_id or "unknown"
            counts[key] = counts.get(key, 0) + 1

        items = []
        for definition in registry.list_workflows():
            items.append(
                WorkflowListItem(
                    workflow_id=definition.workflow_id,
                    display_name=definition.display_name,
                    vertical=definition.vertical,
                    version=definition.version,
                    required_fields=list(definition.required_fields),
                    supported_output_types=list(definition.supported_output_types),
                    default_output_type=definition.default_output_type,
                    supports_post_call_extraction=(
                        definition.supports_post_call_extraction
                    ),
                    session_count=counts.get(definition.workflow_id, 0),
                )
            )
        return items

    def list_sessions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        vertical: str | None = None,
        workflow_id: str | None = None,
        status: str | None = None,
    ) -> list[SessionListItem]:
        """Return recent sessions as admin list rows."""
        sessions = self._sessions.list_recent_sessions(
            limit=limit,
            offset=offset,
            vertical_key=vertical,
            workflow_id=workflow_id,
            status=status,
        )
        org_names = self._organization_names()
        return [self._session_item(session, org_names) for session in sessions]

    def get_session_detail(self, session_id: str) -> SessionDetail | None:
        """Return full admin details for one session."""
        session = self._sessions.load_session(session_id)
        if session is None:
            return None

        org_names = self._organization_names()
        final_output = _final_result(session)
        turns = self.get_turns(session_id)
        audit_metadata = self._audit_metadata(session, final_output)
        proposed_actions = self._actions.list_for_session(session)
        session_item = self._session_item(
            session,
            org_names,
            proposed_action_count=len(proposed_actions),
        )

        detail = SessionDetail(
            session=session_item,
            call_metadata=mask_dashboard_payload(
                {
                    "session_id": session.session_id,
                    "call_sid": session.call_sid,
                    "organization_id": session.organization_id,
                    "phone_number_id": session.phone_number_id,
                    "created_at": session.created_at,
                    "vertical": _vertical_key(session),
                    "workflow_id": session.workflow_id,
                    "workflow_version": session.workflow_version,
                    "status": session_item.status,
                }
            ),
            turns=turns,
            transcript=turns,
            final_output=mask_dashboard_payload(final_output),
            audit_metadata=audit_metadata,
            safety_events=mask_dashboard_payload(_safety_events(session, final_output)),
            healthcare_metadata=None,
            property_management_metadata=None,
            proposed_actions=proposed_actions,
        )

        if _vertical_key(session) == "healthcare":
            detail.healthcare_metadata = mask_dashboard_payload(
                self._healthcare_metadata(session, final_output)
            )
        elif _vertical_key(session) == "property_management":
            detail.property_management_metadata = mask_dashboard_payload(
                self._property_metadata(session, final_output)
            )

        return detail

    def get_turns(self, session_id: str) -> list[TurnDetail]:
        """Return persisted turns for a session."""
        session = self._sessions.load_session(session_id)
        if session is None:
            return []
        try:
            raw_turns = list(self._sessions.get_session_turns(session_id))
        except AttributeError:
            raw_turns = []
        if not raw_turns:
            raw_turns = list(session.decision_trace) or list(session.conversation)
        return [
            TurnDetail.model_validate(_serialize_turn(turn))
            for turn in mask_dashboard_payload(raw_turns_to_payloads(raw_turns))
        ]

    def list_actions(self, session_id: str) -> list[ProposedAction] | None:
        """Return placeholder proposed actions for one session."""
        session = self._sessions.load_session(session_id)
        if session is None:
            return None
        return self._actions.list_for_session(session)

    def transition_action(
        self,
        session_id: str,
        action_id: str,
        status: Any,
    ) -> ProposedAction | None:
        """Change a placeholder action status without touching the session."""
        session = self._sessions.load_session(session_id)
        if session is None:
            return None
        return self._actions.transition(session, action_id, status)

    def summary(self) -> DashboardSummary:
        """Return top-level dashboard metrics."""
        sessions = self.list_sessions(limit=1000)
        by_vertical: dict[str, int] = {}
        for session in sessions:
            vertical = session.vertical or "unknown"
            by_vertical[vertical] = by_vertical.get(vertical, 0) + 1

        all_actions = [
            action
            for raw_session in self._recent_sessions(limit=1000)
            for action in self._actions.list_for_session(raw_session)
        ]
        pending = sum(1 for action in all_actions if action.status.value == "proposed")
        completed = sum(
            1 for action in all_actions if action.status.value == "completed"
        )

        return DashboardSummary(
            total_sessions=len(sessions),
            escalations=sum(1 for session in sessions if session.escalation_required),
            human_reviews=sum(
                1
                for session in sessions
                if (session.disposition or "").upper() == "HUMAN_REVIEW"
                or session.escalation_required
            ),
            sessions_by_vertical=by_vertical,
            pending_actions=pending,
            completed_actions=completed,
            recent_sessions=sessions[:10],
        )

    def _session_item(
        self,
        session: OrchestratorSession,
        org_names: dict[str, str],
        proposed_action_count: int | None = None,
    ) -> SessionListItem:
        final_output = _final_result(session)
        dashboard_record = session.channel_metadata.get("_dashboard_record", {})
        organization_name = (
            org_names.get(session.organization_id or "")
            if session.organization_id
            else None
        )
        action_count = (
            proposed_action_count
            if proposed_action_count is not None
            else len(self._actions.list_for_session(session))
        )
        return SessionListItem(
            session_id=session.session_id,
            created_at=_as_datetime(
                dashboard_record.get("created_at") or session.created_at
            ),
            updated_at=_as_datetime(dashboard_record.get("updated_at")),
            completed_at=_as_datetime(
                dashboard_record.get("ended_at") or dashboard_record.get("finalized_at")
            ),
            organization_id=session.organization_id,
            organization_name=organization_name,
            vertical=_vertical_key(session),
            workflow_id=session.workflow_id,
            workflow_version=session.workflow_version,
            disposition=_disposition(session, final_output),
            confidence_score=_confidence(session, final_output),
            escalation_required=_escalation_required(session, final_output),
            status=dashboard_record.get("status") or _status(session),
            finalization_reason=_finalization_reason(session, final_output),
            proposed_action_count=action_count,
        )

    def _audit_metadata(
        self,
        session: OrchestratorSession,
        final_output: dict[str, Any] | None,
    ) -> AuditMetadata:
        workflow_audit = _workflow_audit_metadata(final_output)
        healthcare_completeness = (
            session.channel_metadata.get("healthcare_intake_completeness")
            or workflow_audit.get("healthcare_intake_completeness")
        )
        blocked_reason = (
            session.channel_metadata.get("healthcare_finalization_blocked_reason")
            or workflow_audit.get("healthcare_finalization_blocked_reason")
            or (
                healthcare_completeness.get("finalization_blocked_reason")
                if isinstance(healthcare_completeness, dict)
                else None
            )
        )
        return AuditMetadata(
            finalization_reason=_finalization_reason(session, final_output),
            healthcare_intake_completeness=healthcare_completeness,
            healthcare_finalization_blocked_reason=blocked_reason,
            rules_triggered=_rules_triggered(session, final_output),
            red_flags_triggered=_red_flags_triggered(session),
            confidence_score=_confidence(session, final_output),
            escalation_required=_escalation_required(session, final_output),
            sbar_available=_sbar_available(session, final_output),
            turn_count=session.turn_count,
            max_turns=session.max_turns,
            audit_trace=mask_dashboard_payload(
                session.audit_trace.model_dump(mode="json")
                if session.audit_trace
                else None
            ),
            workflow_audit_metadata=mask_dashboard_payload(workflow_audit),
            safety_events=mask_dashboard_payload(_safety_events(session, final_output)),
        )

    def _healthcare_metadata(
        self,
        session: OrchestratorSession,
        final_output: dict[str, Any] | None,
    ) -> dict[str, Any]:
        structured = (final_output or {}).get("structured_output") or {}
        sbar = (
            (final_output or {}).get("sbar")
            or structured.get("sbar")
            or structured.get("sbar_report")
            or (session.finalize_output.sbar_report if session.finalize_output else None)
        )
        audit = self._audit_metadata(session, final_output)
        return {
            "healthcare_intake_completeness": audit.healthcare_intake_completeness,
            "healthcare_finalization_blocked_reason": (
                audit.healthcare_finalization_blocked_reason
            ),
            "finalization_reason": audit.finalization_reason,
            "rules_triggered": audit.rules_triggered,
            "red_flags_triggered": audit.red_flags_triggered,
            "confidence_score": audit.confidence_score,
            "escalation_required": audit.escalation_required,
            "sbar_available": bool(sbar),
            "sbar": sbar,
            "disposition": _disposition(session, final_output),
        }

    def _property_metadata(
        self,
        session: OrchestratorSession,
        final_output: dict[str, Any] | None,
    ) -> dict[str, Any]:
        structured = (final_output or {}).get("structured_output") or {}
        work_order = structured.get("work_order") or {}
        intake = structured.get("intake") or {}
        scripted_fields = session.channel_metadata.get("scripted_intake", {}).get(
            "fields",
            {},
        )
        field_source = {**intake, **work_order, **scripted_fields}
        completeness = _required_field_completeness(
            workflow_id=session.workflow_id,
            fields=field_source,
        )
        return {
            "work_order_output": work_order,
            "property_address": work_order.get("property_address")
            or intake.get("property_address")
            or scripted_fields.get("property_address"),
            "unit_number": work_order.get("unit_number")
            or intake.get("unit_number")
            or scripted_fields.get("unit_number"),
            "issue_type": work_order.get("issue_type")
            or intake.get("issue_type")
            or scripted_fields.get("issue_type"),
            "disposition": work_order.get("disposition")
            or (final_output or {}).get("final_disposition"),
            "required_fields_completeness": completeness,
            "recommended_action": work_order.get("recommended_action"),
            "vendor_type": work_order.get("vendor_type"),
        }

    def _list_repository_organizations(self) -> list[Any]:
        try:
            return list(self._sessions.list_organizations())
        except AttributeError:
            return []

    def _organization_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for org in self.list_organizations():
            names[org.organization_id] = org.name
        return names

    def _recent_sessions(self, limit: int) -> list[OrchestratorSession]:
        return self._sessions.list_recent_sessions(limit=limit)


def raw_turns_to_payloads(raw_turns: list[Any]) -> list[dict[str, Any]]:
    """Serialize mixed turn objects before PHI masking."""
    return [_serialize_turn(turn) for turn in raw_turns]


def _organization_item_from_row(row: Any) -> OrganizationListItem:
    if isinstance(row, OrganizationListItem):
        return row
    if isinstance(row, dict):
        return OrganizationListItem(
            organization_id=str(row.get("organization_id") or row.get("id")),
            name=str(row.get("name") or row.get("organization_id") or row.get("id")),
            slug=row.get("slug"),
            status=row.get("status", "active"),
            verticals=list(row.get("verticals") or []),
            workflows=list(row.get("workflows") or []),
            session_count=int(row.get("session_count") or 0),
        )
    org_id = str(getattr(row, "organization_id", None) or getattr(row, "id"))
    return OrganizationListItem(
        organization_id=org_id,
        name=str(getattr(row, "name", org_id)),
        slug=getattr(row, "slug", None),
        status=getattr(row, "status", "active"),
        verticals=list(getattr(row, "verticals", []) or []),
        workflows=list(getattr(row, "workflows", []) or []),
    )


def _counts_by_organization(
    sessions: list[OrchestratorSession],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for session in sessions:
        if not session.organization_id:
            continue
        counts[session.organization_id] = counts.get(session.organization_id, 0) + 1
    return counts


def _required_field_completeness(
    *,
    workflow_id: str | None,
    fields: dict[str, Any],
) -> dict[str, Any]:
    required: list[str] = []
    if workflow_id:
        try:
            definition = ensure_default_workflows_registered().get(workflow_id)
            required = list(definition.get_definition().required_fields)
        except Exception:
            required = []
    missing = [field for field in required if not fields.get(field)]
    present = [field for field in required if fields.get(field)]
    return {
        "required_fields": required,
        "present_fields": present,
        "missing_fields": missing,
        "is_complete": bool(required) and not missing,
    }


def _final_result(session: OrchestratorSession) -> dict[str, Any] | None:
    if session.finalize_output is not None:
        payload = session.finalize_output.model_dump(mode="json")
        payload.setdefault("final_disposition", session.finalize_output.disposition.value)
        payload.setdefault("confidence_score", _confidence(session, payload) or 0.0)
        return payload
    result = session.channel_metadata.get("workflow_final_result")
    if isinstance(result, dict):
        return result
    return None


def _vertical_key(session: OrchestratorSession) -> str | None:
    if session.vertical_key:
        return session.vertical_key
    workflow_id = session.workflow_id or ""
    if workflow_id.startswith("property_management"):
        return "property_management"
    if workflow_id.startswith("healthcare"):
        return "healthcare"
    return None


def _status(session: OrchestratorSession) -> str:
    if session.is_finalized or _final_result(session):
        return "ended"
    return "active"


def _disposition(
    session: OrchestratorSession,
    final_output: dict[str, Any] | None,
) -> str | None:
    final_output = final_output or {}
    if final_output.get("final_disposition"):
        return str(final_output["final_disposition"])
    if final_output.get("disposition"):
        return str(final_output["disposition"])
    if session.finalize_output is not None:
        return session.finalize_output.disposition.value
    if session.decision_trace:
        return session.decision_trace[-1].disposition
    return None


def _confidence(
    session: OrchestratorSession,
    final_output: dict[str, Any] | None,
) -> float | None:
    final_output = final_output or {}
    value = final_output.get("confidence_score")
    if value is not None:
        return float(value)
    if session.decision_trace:
        return session.decision_trace[-1].confidence_score
    return None


def _finalization_reason(
    session: OrchestratorSession,
    final_output: dict[str, Any] | None,
) -> str | None:
    workflow_audit = _workflow_audit_metadata(final_output)
    return (
        session.finalization_reason
        or workflow_audit.get("finalization_reason")
        or ((final_output or {}).get("structured_output") or {}).get(
            "finalization_reason"
        )
        or _last_trace_finalization_reason(session)
    )


def _workflow_audit_metadata(
    final_output: dict[str, Any] | None,
) -> dict[str, Any]:
    audit = (final_output or {}).get("audit_metadata")
    return audit if isinstance(audit, dict) else {}


def _last_trace_finalization_reason(session: OrchestratorSession) -> str | None:
    for entry in reversed(session.decision_trace):
        if entry.finalization_reason:
            return entry.finalization_reason
    return None


def _rules_triggered(
    session: OrchestratorSession,
    final_output: dict[str, Any] | None,
) -> list[Any]:
    rules: list[Any] = []
    for entry in session.decision_trace:
        rules.extend(entry.rules_triggered)
    rules.extend((final_output or {}).get("rules_triggered") or [])
    if session.audit_trace is not None:
        rules.extend(session.audit_trace.deterministic_rules_triggered)
    return _dedupe(rules)


def _red_flags_triggered(session: OrchestratorSession) -> list[Any]:
    flags: list[Any] = []
    for entry in session.decision_trace:
        flags.extend(entry.red_flags_triggered)
    return _dedupe(flags)


def _safety_events(
    session: OrchestratorSession,
    final_output: dict[str, Any] | None,
) -> list[Any]:
    events = [flag.model_dump(mode="json") for flag in session.safety_flags]
    events.extend((final_output or {}).get("safety_events") or [])
    events.extend((final_output or {}).get("llm_safety_flags") or [])
    return events


def _escalation_required(
    session: OrchestratorSession,
    final_output: dict[str, Any] | None,
) -> bool:
    if any(entry.escalation_required for entry in session.decision_trace):
        return True
    disposition = (_disposition(session, final_output) or "").upper()
    return disposition in {"ER_NOW", "URGENT", "HUMAN_REVIEW", "EMERGENCY"}


def _sbar_available(
    session: OrchestratorSession,
    final_output: dict[str, Any] | None,
) -> bool:
    structured = (final_output or {}).get("structured_output") or {}
    return bool(
        (final_output or {}).get("sbar")
        or (final_output or {}).get("sbar_report")
        or structured.get("sbar")
        or structured.get("sbar_report")
        or (session.finalize_output and session.finalize_output.sbar_report)
    )


def _serialize_turn(turn: Any) -> dict[str, Any]:
    if hasattr(turn, "turn_index"):
        protocol_hits = getattr(turn, "protocol_hits", None) or []
        return {
            "turn_index": getattr(turn, "turn_index", None),
            "timestamp": _as_datetime(getattr(turn, "timestamp", None)),
            "caller_text": getattr(turn, "user_text", None),
            "assistant_text": getattr(turn, "system_text", None),
            "red_flags_triggered": getattr(turn, "red_flags_triggered", None) or [],
            "rules_triggered": getattr(turn, "rules_triggered", None) or [],
            "protocol_hits": _model_dump_list(protocol_hits),
            "protocol_citations": getattr(turn, "protocol_citations", None) or [],
            "confidence_score": getattr(turn, "confidence_score", None),
            "disposition": getattr(turn, "disposition", None),
            "escalation_required": getattr(turn, "escalation_required", None),
        }
    if hasattr(turn, "turn_number"):
        data = turn.model_dump(mode="json")
        return {
            "turn_index": data.get("turn_number"),
            "timestamp": _as_datetime(data.get("timestamp")),
            "caller_text": data.get("user_text"),
            "assistant_text": data.get("system_response"),
            "red_flags_triggered": data.get("red_flags_triggered") or [],
            "rules_triggered": data.get("rules_triggered") or [],
            "protocol_hits": data.get("protocol_hits") or [],
            "protocol_citations": data.get("protocol_citations") or [],
            "confidence_score": data.get("confidence_score"),
            "disposition": data.get("disposition"),
            "escalation_required": data.get("escalation_required"),
            "finalization_reason": data.get("finalization_reason"),
        }
    if hasattr(turn, "model_dump"):
        data = turn.model_dump(mode="json")
        return {
            "timestamp": _as_datetime(data.get("timestamp")),
            "role": data.get("role"),
            "text": data.get("text"),
        }
    if isinstance(turn, dict):
        payload = dict(turn)
        if payload.get("timestamp") is not None:
            payload["timestamp"] = _as_datetime(payload["timestamp"])
        return payload
    return {"text": str(turn)}


def _model_dump_list(items: list[Any]) -> list[Any]:
    return [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in items]


def _dedupe(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for item in items:
        key = repr(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
