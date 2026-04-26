"""Repository helpers for tenant routing data."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.platform.workflows.schemas import ResolvedWorkflowRoute
from src.storage.models import (
    OrganizationModel,
    OrganizationWorkflowModel,
    PhoneNumberModel,
    VerticalModel,
)


class OrganizationRepository:
    """Read/write access for routing foundation tables."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._SessionFactory = session_factory

    def resolve_active_phone_number(
        self, e164_number: str
    ) -> ResolvedWorkflowRoute | None:
        """Resolve an active phone number to its organization workflow route."""

        with self._SessionFactory() as db:
            phone = db.execute(
                select(PhoneNumberModel).where(
                    PhoneNumberModel.e164_number == e164_number,
                    PhoneNumberModel.status == "active",
                )
            ).scalar_one_or_none()
            if phone is None:
                return None

            organization = db.get(OrganizationModel, phone.organization_id)
            vertical = db.get(VerticalModel, phone.vertical_id)
            if (
                organization is None
                or vertical is None
                or organization.status != "active"
                or vertical.status != "active"
            ):
                return None

            org_workflow = db.execute(
                select(OrganizationWorkflowModel).where(
                    OrganizationWorkflowModel.organization_id == phone.organization_id,
                    OrganizationWorkflowModel.vertical_id == phone.vertical_id,
                    OrganizationWorkflowModel.workflow_id == phone.workflow_id,
                    OrganizationWorkflowModel.status == "active",
                )
            ).scalar_one_or_none()
            if org_workflow is None:
                return None

            workflow_version = org_workflow.workflow_version
            config_json = dict(org_workflow.config_json or {})

            return ResolvedWorkflowRoute(
                organization_id=organization.id,
                organization_name=organization.name,
                vertical_key=vertical.key,
                workflow_id=phone.workflow_id,
                workflow_version=workflow_version,
                phone_number_id=phone.id,
                config_json=config_json,
            )
