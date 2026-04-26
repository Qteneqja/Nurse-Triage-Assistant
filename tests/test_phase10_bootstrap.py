import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.platform.organizations.bootstrap import (
    DefaultRouteBootstrapSettings,
    bootstrap_default_route,
)
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


def _settings() -> DefaultRouteBootstrapSettings:
    return DefaultRouteBootstrapSettings(
        organization_name="Pilot Clinic",
        organization_slug="pilot-clinic",
        vertical_key="healthcare",
        workflow_id="healthcare_triage_v1",
        workflow_version="v1",
        twilio_phone_number="+15551234567",
    )


def test_default_route_bootstrap_is_idempotent():
    sf = _session_factory()

    first = bootstrap_default_route(sf, _settings())
    second = bootstrap_default_route(sf, _settings())

    assert set(first.created) == {
        "organization",
        "vertical",
        "organization_workflow",
        "phone_number",
    }
    assert second.created == []
    assert second.updated == []
    assert first.organization_id == second.organization_id
    assert first.phone_number_id == second.phone_number_id

    with sf() as db:
        assert len(db.execute(select(OrganizationModel)).scalars().all()) == 1
        assert len(db.execute(select(VerticalModel)).scalars().all()) == 1
        assert len(db.execute(select(OrganizationWorkflowModel)).scalars().all()) == 1
        assert len(db.execute(select(PhoneNumberModel)).scalars().all()) == 1


def test_default_route_bootstrap_rejects_phone_number_collision():
    sf = _session_factory()
    bootstrap_default_route(sf, _settings())

    conflicting = DefaultRouteBootstrapSettings(
        organization_name="Other Clinic",
        organization_slug="other-clinic",
        vertical_key="healthcare",
        workflow_id="healthcare_triage_v1",
        workflow_version="v1",
        twilio_phone_number="+15551234567",
    )

    with pytest.raises(RuntimeError, match="already assigned"):
        bootstrap_default_route(sf, conflicting)


def test_bootstrap_environment_requires_production_phone(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEFAULT_ORGANIZATION_NAME", "Pilot Clinic")
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", "pilot-clinic")
    monkeypatch.setenv("DEFAULT_VERTICAL_KEY", "healthcare")
    monkeypatch.setenv("DEFAULT_WORKFLOW_ID", "healthcare_triage_v1")
    monkeypatch.setenv("DEFAULT_WORKFLOW_VERSION", "v1")
    monkeypatch.delenv("DEFAULT_TWILIO_PHONE_NUMBER", raising=False)

    with pytest.raises(RuntimeError, match="DEFAULT_TWILIO_PHONE_NUMBER"):
        DefaultRouteBootstrapSettings.from_environment()
