"""Pydantic schemas for organization routing data."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OrganizationSummary(BaseModel):
    id: str
    name: str
    slug: str
    status: str = "active"


class VerticalSummary(BaseModel):
    id: str
    key: str
    display_name: str
    status: str = "active"


class PhoneNumberRoute(BaseModel):
    id: str
    organization_id: str
    organization_name: str
    vertical_key: str
    workflow_id: str
    workflow_version: str
    e164_number: str
    config_json: dict[str, Any] = Field(default_factory=dict)
