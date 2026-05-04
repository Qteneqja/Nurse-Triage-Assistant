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
from src.verticals.property_management.constants import (
    PROPERTY_MAINTENANCE_WORKFLOW_ID,
)


def _session_factory():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_property_phone_number_resolves_to_property_workflow():
    sf = _session_factory()
    with sf() as db:
        org = OrganizationModel(
            id="org-property",
            name="Pilot Property Co",
            slug="pilot-property",
            status="active",
        )
        vertical = VerticalModel(
            id="vertical-property",
            key="property_management",
            display_name="Property Management",
            status="active",
        )
        workflow = OrganizationWorkflowModel(
            id="owf-property",
            organization_id="org-property",
            vertical_id="vertical-property",
            workflow_id=PROPERTY_MAINTENANCE_WORKFLOW_ID,
            workflow_version="v1",
            is_default=True,
            config_json={"portfolio": "north"},
            status="active",
        )
        phone = PhoneNumberModel(
            id="phone-property",
            organization_id="org-property",
            vertical_id="vertical-property",
            workflow_id=PROPERTY_MAINTENANCE_WORKFLOW_ID,
            e164_number="+15559870000",
            status="active",
        )
        db.add_all([org, vertical, workflow, phone])
        db.commit()

    resolver = WorkflowRouteResolver(repository=OrganizationRepository(sf))
    route = resolver.resolve("+1 (555) 987-0000")

    assert route.vertical_key == "property_management"
    assert route.workflow_id == PROPERTY_MAINTENANCE_WORKFLOW_ID
    assert route.phone_number_id == "phone-property"
    assert route.config_json == {"portfolio": "north"}


def test_unknown_phone_does_not_accidentally_select_property_workflow():
    resolver = WorkflowRouteResolver(repository=None)

    with (
        patch("src.platform.workflows.router.config.ENVIRONMENT", "development"),
        patch(
            "src.platform.workflows.router.config.ENABLE_DEFAULT_WORKFLOW_ROUTE",
            True,
        ),
    ):
        route = resolver.resolve("+15550009999")

    assert route.workflow_id == "healthcare_triage_v1"
    assert route.vertical_key == "healthcare"
    assert route.workflow_id != PROPERTY_MAINTENANCE_WORKFLOW_ID
