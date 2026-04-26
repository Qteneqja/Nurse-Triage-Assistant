from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.platform.organizations.repository import OrganizationRepository
from src.platform.workflows.router import WorkflowRouteResolver
from src.storage.models import (
    Base,
    OrganizationModel,
    OrganizationWorkflowModel,
    PhoneNumberModel,
    VerticalModel,
)


def _session_factory():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_route(session_factory, *, phone_status: str = "active"):
    with session_factory() as db:
        org = OrganizationModel(
            id="org-1",
            name="Pilot Clinic",
            slug="pilot-clinic",
            status="active",
        )
        vertical = VerticalModel(
            id="vertical-1",
            key="healthcare",
            display_name="Healthcare",
            status="active",
        )
        workflow = OrganizationWorkflowModel(
            id="owf-1",
            organization_id="org-1",
            vertical_id="vertical-1",
            workflow_id="healthcare_triage_v1",
            workflow_version="v1",
            is_default=True,
            config_json={"clinic": "pilot"},
            status="active",
        )
        phone = PhoneNumberModel(
            id="phone-1",
            organization_id="org-1",
            vertical_id="vertical-1",
            workflow_id="healthcare_triage_v1",
            e164_number="+15551234567",
            status=phone_status,
        )
        db.add_all([org, vertical, workflow, phone])
        db.commit()


def _deactivate_org_workflow(session_factory):
    with session_factory() as db:
        workflow = db.get(OrganizationWorkflowModel, "owf-1")
        workflow.status = "inactive"
        db.commit()


def test_known_phone_number_resolves_to_configured_workflow():
    sf = _session_factory()
    _seed_route(sf)
    resolver = WorkflowRouteResolver(repository=OrganizationRepository(sf))

    route = resolver.resolve("+1 (555) 123-4567")

    assert route.organization_id == "org-1"
    assert route.organization_name == "Pilot Clinic"
    assert route.vertical_key == "healthcare"
    assert route.workflow_id == "healthcare_triage_v1"
    assert route.workflow_version == "v1"
    assert route.phone_number_id == "phone-1"
    assert route.config_json == {"clinic": "pilot"}
    assert route.fallback_used is False
    assert route.audit_metadata["routing_source"] == "phone_number"


def test_unknown_phone_falls_back_to_default_when_enabled():
    sf = _session_factory()
    resolver = WorkflowRouteResolver(repository=OrganizationRepository(sf))

    with (
        patch("src.platform.workflows.router.config.ENVIRONMENT", "development"),
        patch(
            "src.platform.workflows.router.config.ENABLE_DEFAULT_WORKFLOW_ROUTE",
            True,
        ),
    ):
        route = resolver.resolve("+15550000000")

    assert route.fallback_used is True
    assert route.workflow_id == "healthcare_triage_v1"
    assert route.safe_response_required is False
    assert route.audit_metadata["routing_source"] == "default_fallback"
    assert route.audit_metadata["fallback_reason"] == "missing_phone_number_route"


def test_inactive_phone_number_does_not_resolve_as_active():
    sf = _session_factory()
    _seed_route(sf, phone_status="inactive")
    resolver = WorkflowRouteResolver(repository=OrganizationRepository(sf))

    with patch("src.platform.workflows.router.config.ENVIRONMENT", "development"):
        route = resolver.resolve("+15551234567")

    assert route.fallback_used is True
    assert route.phone_number_id is None


def test_inactive_organization_workflow_does_not_resolve_as_active():
    sf = _session_factory()
    _seed_route(sf)
    _deactivate_org_workflow(sf)
    resolver = WorkflowRouteResolver(repository=OrganizationRepository(sf))

    with (
        patch("src.platform.workflows.router.config.ENVIRONMENT", "production"),
        patch(
            "src.platform.workflows.router.config.ENABLE_DEFAULT_WORKFLOW_ROUTE",
            False,
        ),
    ):
        route = resolver.resolve("+15551234567")

    assert route.fallback_used is True
    assert route.safe_response_required is True
    assert route.phone_number_id is None


def test_production_fallback_disabled_returns_safe_route():
    resolver = WorkflowRouteResolver(repository=None)

    with (
        patch("src.platform.workflows.router.config.ENVIRONMENT", "production"),
        patch(
            "src.platform.workflows.router.config.ENABLE_DEFAULT_WORKFLOW_ROUTE",
            False,
        ),
    ):
        route = resolver.resolve("+15550000000")

    assert route.fallback_used is True
    assert route.safe_response_required is True
    assert route.fallback_reason == "routing_missing_default_disabled"


def test_production_workflow_hint_is_ignored_unless_explicitly_enabled():
    resolver = WorkflowRouteResolver(repository=None)

    with (
        patch("src.platform.workflows.router.config.ENVIRONMENT", "production"),
        patch(
            "src.platform.workflows.router.config.ENABLE_WORKFLOW_HINT_ROUTE",
            False,
        ),
        patch(
            "src.platform.workflows.router.config.ENABLE_DEFAULT_WORKFLOW_ROUTE",
            False,
        ),
    ):
        route = resolver.resolve(
            "+15550000000",
            workflow_hint="some_other_workflow_v1",
        )

    assert route.workflow_id == "healthcare_triage_v1"
    assert route.safe_response_required is True
    assert route.fallback_reason == "routing_missing_default_disabled"
