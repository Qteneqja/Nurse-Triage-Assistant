"""Schemas for post-call structured extraction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ExtractionResult(BaseModel):
    """Structured analytics result produced after a workflow completes."""

    extraction_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    organization_id: str | None = None
    vertical: str
    workflow_id: str
    schema_version: str
    summary: str | None = None
    entities: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_model_output: dict[str, Any] | str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
