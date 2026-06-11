"""Internal/admin enrichment view (shadow mode) — the upsell demo surface.

Gated by the dashboard auth pattern PLUS the enrichment master flag (404
when ENRICHMENT_ENABLED is false). Every payload is labeled as PREVIEW so
shadow-mode output is never mistaken for committed pilot functionality.
This is NOT the customer-facing pilot dashboard; the core record never
depends on anything served here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

import src.config as config
from src.api.dashboard import require_dashboard_api_access
from src.enrichment.redaction import restore_tokens
from src.storage.session_repository import get_session_repository

logger = logging.getLogger(__name__)

PREVIEW_LABEL = "ENRICHMENT PREVIEW (shadow mode) — not part of the core pilot record"


def require_enrichment_enabled() -> None:
    if not getattr(config, "ENRICHMENT_ENABLED", False):
        raise HTTPException(status_code=404, detail="Enrichment is disabled")


router = APIRouter(
    dependencies=[
        Depends(require_dashboard_api_access),
        Depends(require_enrichment_enabled),
    ]
)


def _present(row: dict[str, Any]) -> dict[str, Any]:
    """Add locally-rendered variants (tokens restored) for display.

    Token restoration happens HERE, locally, behind the auth gate — the
    provider never saw the real values in redact mode.
    """
    out = dict(row)
    tokens = row.get("tokens_map") or {}
    payload = row.get("payload") or {}
    if (
        row.get("feature") == "followup"
        and row.get("status") == "completed"
        and getattr(config, "DASHBOARD_RECORDS_SHOW_CONTACT", True)
    ):
        out["rendered_drafts"] = {
            key: restore_tokens(str(payload.get(key) or ""), tokens)
            for key in ("sms_draft", "email_subject", "email_draft")
        }
    out.pop("tokens_map", None)  # never ship the PII map to the browser list
    out["preview"] = True
    return out


@router.get("/sessions/{session_id}")
async def enrichment_for_session(session_id: str) -> dict[str, Any]:
    repo = get_session_repository()
    rows = repo.get_enrichment_results(session_id)
    return {
        "label": PREVIEW_LABEL,
        "preview": True,
        "session_id": session_id,
        "count": len(rows),
        "results": [_present(r) for r in rows],
    }


@router.get("/recent")
async def recent_enrichment(
    limit: int = Query(default=50, ge=1, le=200),
    feature: str | None = None,
) -> dict[str, Any]:
    repo = get_session_repository()
    rows = repo.list_enrichment_results(limit=limit, feature=feature)
    return {
        "label": PREVIEW_LABEL,
        "preview": True,
        "count": len(rows),
        "results": [_present(r) for r in rows],
    }


@router.get("/insights")
async def enrichment_insights(
    commentary: bool = Query(default=False),
) -> dict[str, Any]:
    if not getattr(config, "ENRICH_INSIGHTS", True):
        raise HTTPException(status_code=404, detail="Insights feature is disabled")
    from src.enrichment.insights import compute_insights, insights_commentary

    repo = get_session_repository()
    sessions = repo.list_recent_sessions(
        limit=1000, vertical_key="automotive_collision"
    )
    aggregates = compute_insights(sessions)
    payload: dict[str, Any] = {
        "label": PREVIEW_LABEL,
        "preview": True,
        "insights": aggregates,
        "commentary": None,
    }
    if commentary and aggregates.get("reliable"):
        payload["commentary"] = await insights_commentary(aggregates)
    return payload
