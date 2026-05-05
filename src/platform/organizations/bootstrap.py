"""Idempotent bootstrap for the default workflow route."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.storage.models import (
    OrganizationModel,
    OrganizationWorkflowModel,
    PhoneNumberModel,
    VerticalModel,
)


@dataclass(frozen=True)
class DefaultRouteBootstrapSettings:
    organization_name: str
    organization_slug: str
    vertical_key: str
    workflow_id: str
    workflow_version: str
    twilio_phone_number: str | None = None
    provider: str = "twilio"

    @classmethod
    def from_environment(
        cls,
        *,
        production: bool | None = None,
    ) -> "DefaultRouteBootstrapSettings":
        production = production if production is not None else _is_production()
        required = [
            "DEFAULT_VERTICAL_KEY",
            "DEFAULT_WORKFLOW_ID",
            "DEFAULT_WORKFLOW_VERSION",
        ]
        if production:
            required.extend(
                [
                    "DEFAULT_ORGANIZATION_NAME",
                    "DEFAULT_ORGANIZATION_SLUG",
                    "DEFAULT_TWILIO_PHONE_NUMBER",
                ]
            )

        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise RuntimeError(
                "Missing required bootstrap environment variables: "
                + ", ".join(sorted(missing))
            )

        return cls(
            organization_name=os.getenv(
                "DEFAULT_ORGANIZATION_NAME",
                "Default Healthcare Organization",
            ),
            organization_slug=os.getenv(
                "DEFAULT_ORGANIZATION_SLUG",
                "default-healthcare",
            ),
            vertical_key=os.getenv("DEFAULT_VERTICAL_KEY", "healthcare"),
            workflow_id=os.getenv("DEFAULT_WORKFLOW_ID", "healthcare_triage_v1"),
            workflow_version=os.getenv("DEFAULT_WORKFLOW_VERSION", "v1"),
            twilio_phone_number=os.getenv("DEFAULT_TWILIO_PHONE_NUMBER") or None,
        )


class DefaultRouteBootstrapResult(BaseModel):
    organization_id: str
    vertical_id: str
    organization_workflow_id: str
    phone_number_id: str | None = None
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)


def bootstrap_default_route(
    session_factory: sessionmaker,
    settings: DefaultRouteBootstrapSettings,
) -> DefaultRouteBootstrapResult:
    """Create or update default organization, vertical, workflow, and phone route."""

    created: list[str] = []
    updated: list[str] = []
    normalized_phone = _normalize_phone(settings.twilio_phone_number)

    with session_factory() as db:
        organization = _get_or_create_organization(db, settings, created, updated)
        vertical = _get_or_create_vertical(db, settings, created, updated)
        org_workflow = _get_or_create_org_workflow(
            db,
            settings,
            organization,
            vertical,
            created,
            updated,
        )
        phone = None
        if normalized_phone:
            phone = _get_or_create_phone_number(
                db,
                settings,
                normalized_phone,
                organization,
                vertical,
                created,
                updated,
            )
        db.commit()

        return DefaultRouteBootstrapResult(
            organization_id=organization.id,
            vertical_id=vertical.id,
            organization_workflow_id=org_workflow.id,
            phone_number_id=phone.id if phone else None,
            created=created,
            updated=updated,
        )


def _get_or_create_organization(
    db: Session,
    settings: DefaultRouteBootstrapSettings,
    created: list[str],
    updated: list[str],
) -> OrganizationModel:
    organization = db.execute(
        select(OrganizationModel).where(
            OrganizationModel.slug == settings.organization_slug
        )
    ).scalar_one_or_none()
    if organization is None:
        organization = OrganizationModel(
            id=str(uuid.uuid4()),
            name=settings.organization_name,
            slug=settings.organization_slug,
            status="active",
        )
        db.add(organization)
        created.append("organization")
    else:
        changed = False
        if organization.name != settings.organization_name:
            organization.name = settings.organization_name
            changed = True
        if organization.status != "active":
            organization.status = "active"
            changed = True
        if changed:
            updated.append("organization")
    return organization


def _get_or_create_vertical(
    db: Session,
    settings: DefaultRouteBootstrapSettings,
    created: list[str],
    updated: list[str],
) -> VerticalModel:
    vertical = db.execute(
        select(VerticalModel).where(VerticalModel.key == settings.vertical_key)
    ).scalar_one_or_none()
    display_name = settings.vertical_key.replace("_", " ").title()
    if vertical is None:
        vertical = VerticalModel(
            id=str(uuid.uuid4()),
            key=settings.vertical_key,
            display_name=display_name,
            status="active",
        )
        db.add(vertical)
        created.append("vertical")
    elif vertical.status != "active":
        vertical.status = "active"
        updated.append("vertical")
    return vertical


def _get_or_create_org_workflow(
    db: Session,
    settings: DefaultRouteBootstrapSettings,
    organization: OrganizationModel,
    vertical: VerticalModel,
    created: list[str],
    updated: list[str],
) -> OrganizationWorkflowModel:
    org_workflow = db.execute(
        select(OrganizationWorkflowModel).where(
            OrganizationWorkflowModel.organization_id == organization.id,
            OrganizationWorkflowModel.vertical_id == vertical.id,
            OrganizationWorkflowModel.workflow_id == settings.workflow_id,
        )
    ).scalar_one_or_none()
    if org_workflow is None:
        org_workflow = OrganizationWorkflowModel(
            id=str(uuid.uuid4()),
            organization_id=organization.id,
            vertical_id=vertical.id,
            workflow_id=settings.workflow_id,
            workflow_version=settings.workflow_version,
            is_default=True,
            config_json={},
            status="active",
        )
        db.add(org_workflow)
        created.append("organization_workflow")
    else:
        changed = False
        if org_workflow.workflow_version != settings.workflow_version:
            org_workflow.workflow_version = settings.workflow_version
            changed = True
        if not org_workflow.is_default:
            org_workflow.is_default = True
            changed = True
        if org_workflow.status != "active":
            org_workflow.status = "active"
            changed = True
        if changed:
            updated.append("organization_workflow")
    return org_workflow


def _get_or_create_phone_number(
    db: Session,
    settings: DefaultRouteBootstrapSettings,
    normalized_phone: str,
    organization: OrganizationModel,
    vertical: VerticalModel,
    created: list[str],
    updated: list[str],
) -> PhoneNumberModel:
    phone = db.execute(
        select(PhoneNumberModel).where(PhoneNumberModel.e164_number == normalized_phone)
    ).scalar_one_or_none()
    if phone is None:
        phone = PhoneNumberModel(
            id=str(uuid.uuid4()),
            organization_id=organization.id,
            vertical_id=vertical.id,
            workflow_id=settings.workflow_id,
            e164_number=normalized_phone,
            provider=settings.provider,
            label="Default inbound route",
            status="active",
        )
        db.add(phone)
        created.append("phone_number")
    else:
        if phone.organization_id != organization.id or phone.vertical_id != vertical.id:
            raise RuntimeError(
                "DEFAULT_TWILIO_PHONE_NUMBER is already assigned to another route"
            )
        changed = False
        if phone.workflow_id != settings.workflow_id:
            phone.workflow_id = settings.workflow_id
            changed = True
        if phone.provider != settings.provider:
            phone.provider = settings.provider
            changed = True
        if phone.status != "active":
            phone.status = "active"
            changed = True
        if changed:
            updated.append("phone_number")
    return phone


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[\s().-]+", "", value.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        raise RuntimeError("DEFAULT_TWILIO_PHONE_NUMBER must be E.164 formatted")
    return cleaned


def _is_production() -> bool:
    return (
        os.getenv("APP_ENV") == "production" or os.getenv("ENVIRONMENT") == "production"
    )
