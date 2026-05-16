"""Phase 12 proposed-action service.

The service intentionally generates deterministic placeholder actions only.
It does not call OpenClaw, external APIs, webhooks, ticketing systems, or
clinical decision code.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.admin.schemas import ActionStatus, ProposedAction
from src.orchestrator.schemas import OrchestratorSession


_ACTION_STATUSES: dict[str, ActionStatus] = {}
_ACTION_UPDATED_AT: dict[str, datetime] = {}


class ProposedActionService:
    """Generate and manage internal placeholder post-call actions."""

    def list_for_session(self, session: OrchestratorSession) -> list[ProposedAction]:
        """Return deterministic actions for a finalized session."""
        if not _is_finalized_enough_for_actions(session):
            return []

        vertical = _vertical_key(session)
        if vertical == "healthcare":
            templates = [
                (
                    "review_sbar",
                    "clinical_review",
                    "Review SBAR",
                    "Internal reminder to review the generated SBAR handoff.",
                ),
                (
                    "call_patient_back",
                    "callback",
                    "Call patient back",
                    "Internal reminder for a human team member to call the patient.",
                ),
                (
                    "escalate_to_nurse_queue",
                    "queue_escalation",
                    "Escalate to nurse queue",
                    "Internal placeholder for nurse queue escalation review.",
                ),
            ]
        elif vertical == "property_management":
            templates = [
                (
                    "create_work_order",
                    "work_order",
                    "Create work order",
                    "Internal placeholder for creating a maintenance work order.",
                ),
                (
                    "notify_property_manager",
                    "notification",
                    "Notify property manager",
                    "Internal reminder to notify the responsible property manager.",
                ),
                (
                    "schedule_repair",
                    "scheduling",
                    "Schedule repair",
                    "Internal placeholder for scheduling vendor or repair follow-up.",
                ),
            ]
        else:
            templates = [
                (
                    "review_final_output",
                    "manual_review",
                    "Review final output",
                    "Internal reminder to review the finalized workflow output.",
                )
            ]

        return [
            self._build_action(
                session=session,
                action_key=action_key,
                action_type=action_type,
                title=title,
                description=description,
            )
            for action_key, action_type, title, description in templates
        ]

    def get_for_session(
        self,
        session: OrchestratorSession,
        action_id: str,
    ) -> ProposedAction | None:
        """Return one generated action by id."""
        for action in self.list_for_session(session):
            if action.action_id == action_id:
                return action
        return None

    def transition(
        self,
        session: OrchestratorSession,
        action_id: str,
        status: ActionStatus,
    ) -> ProposedAction | None:
        """Change only the action status for an internal placeholder action."""
        action = self.get_for_session(session, action_id)
        if action is None:
            return None

        now = datetime.now(UTC)
        _ACTION_STATUSES[action_id] = status
        _ACTION_UPDATED_AT[action_id] = now
        action.status = status
        action.updated_at = now
        action.status_reason = f"Marked {status.value} by admin API"
        return action

    def reset(self) -> None:
        """Clear process-local status state. Intended for tests."""
        _ACTION_STATUSES.clear()
        _ACTION_UPDATED_AT.clear()

    def _build_action(
        self,
        *,
        session: OrchestratorSession,
        action_key: str,
        action_type: str,
        title: str,
        description: str,
    ) -> ProposedAction:
        action_id = f"{session.session_id}:{action_key}"
        status = _ACTION_STATUSES.get(action_id, ActionStatus.PROPOSED)
        return ProposedAction(
            action_id=action_id,
            session_id=session.session_id,
            organization_id=session.organization_id,
            vertical=_vertical_key(session),
            workflow_id=session.workflow_id,
            action_type=action_type,
            title=title,
            description=description,
            payload={
                "placeholder": True,
                "action_key": action_key,
                "external_execution_enabled": False,
            },
            status=status,
            updated_at=_ACTION_UPDATED_AT.get(action_id),
        )


def get_proposed_action_service() -> ProposedActionService:
    """Return the process-local proposed-action service."""
    return _ACTION_SERVICE


def reset_proposed_action_service() -> None:
    """Reset process-local proposed-action state for tests."""
    _ACTION_SERVICE.reset()


def _is_finalized_enough_for_actions(session: OrchestratorSession) -> bool:
    if session.is_finalized:
        return True
    return bool(
        session.finalize_output or session.channel_metadata.get("workflow_final_result")
    )


def _vertical_key(session: OrchestratorSession) -> str | None:
    if session.vertical_key:
        return session.vertical_key
    workflow_id = session.workflow_id or ""
    if workflow_id.startswith("property_management"):
        return "property_management"
    if workflow_id.startswith("healthcare"):
        return "healthcare"
    return None


_ACTION_SERVICE = ProposedActionService()
