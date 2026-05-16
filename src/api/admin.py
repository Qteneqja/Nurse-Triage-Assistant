"""Phase 12 admin/dashboard API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from src.admin.actions import get_proposed_action_service
from src.admin.schemas import (
    ActionStatus,
    DashboardSummary,
    OrganizationListItem,
    ProposedAction,
    SessionDetail,
    SessionListItem,
    TurnDetail,
    WorkflowListItem,
)
from src.admin.service import AdminDashboardService
from src.api.dashboard import require_dashboard_api_access
from src.storage.session_repository import get_session_repository


router = APIRouter(dependencies=[Depends(require_dashboard_api_access)])


def get_admin_dashboard_service() -> AdminDashboardService:
    """Build the admin service from process-local repositories."""
    return AdminDashboardService(
        session_repository=get_session_repository(),
        action_service=get_proposed_action_service(),
    )


@router.get("/summary", response_model=DashboardSummary)
async def get_admin_summary(
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> DashboardSummary:
    """Return dashboard home metrics."""
    return service.summary()


@router.get("/organizations", response_model=list[OrganizationListItem])
async def list_admin_organizations(
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> list[OrganizationListItem]:
    """List organizations visible to the admin shell."""
    return service.list_organizations()


@router.get("/workflows", response_model=list[WorkflowListItem])
async def list_admin_workflows(
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> list[WorkflowListItem]:
    """List registered routed workflows."""
    return service.list_workflows()


@router.get("/sessions", response_model=list[SessionListItem])
async def list_admin_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    vertical: str | None = Query(default=None),
    workflow_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> list[SessionListItem]:
    """List recent calls/sessions for operators."""
    return service.list_sessions(
        limit=limit,
        offset=offset,
        vertical=vertical,
        workflow_id=workflow_id,
        status=status,
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_admin_session(
    session_id: str,
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> SessionDetail:
    """Return full session detail including audit metadata."""
    detail = service.get_session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@router.get("/sessions/{session_id}/turns", response_model=list[TurnDetail])
async def get_admin_session_turns(
    session_id: str,
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> list[TurnDetail]:
    """Return transcript/turn detail for one session."""
    if service.get_session_detail(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return service.get_turns(session_id)


@router.get("/sessions/{session_id}/actions", response_model=list[ProposedAction])
async def get_admin_session_actions(
    session_id: str,
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> list[ProposedAction]:
    """Return deterministic placeholder actions for a finalized session."""
    actions = service.list_actions(session_id)
    if actions is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return actions


@router.post(
    "/sessions/{session_id}/actions/{action_id}/approve",
    response_model=ProposedAction,
)
async def approve_admin_action(
    session_id: str,
    action_id: str,
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> ProposedAction:
    """Approve a placeholder action without executing it."""
    return _transition_or_404(service, session_id, action_id, ActionStatus.APPROVED)


@router.post(
    "/sessions/{session_id}/actions/{action_id}/reject",
    response_model=ProposedAction,
)
async def reject_admin_action(
    session_id: str,
    action_id: str,
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> ProposedAction:
    """Reject a placeholder action without executing it."""
    return _transition_or_404(service, session_id, action_id, ActionStatus.REJECTED)


@router.post(
    "/sessions/{session_id}/actions/{action_id}/complete",
    response_model=ProposedAction,
)
async def complete_admin_action(
    session_id: str,
    action_id: str,
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> ProposedAction:
    """Mark a placeholder action complete without executing external work."""
    return _transition_or_404(service, session_id, action_id, ActionStatus.COMPLETED)


def _transition_or_404(
    service: AdminDashboardService,
    session_id: str,
    action_id: str,
    status: ActionStatus,
) -> ProposedAction:
    action = service.transition_action(session_id, action_id, status)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    return action
