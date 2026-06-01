"""
Reports API — Triage Report Listing and Retrieval

These endpoints are NOT behind Twilio signature validation because they
are consumed by dashboards, monitoring tools, and manual review — not
by Twilio webhooks.

Read-only access to locally-stored and blob-stored triage reports.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from src.api.dashboard import require_dashboard_api_access

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_dashboard_api_access)])

# Same reports directory used by the background report generator
REPORTS_DIR = Path("reports")


def _find_report_files(identifier: str) -> tuple[Path | None, Path | None]:
    """Find report JSON and TXT files by session_id, filename stem, or partial match.

    Searches recursively through year/month subdirectories.

    Search order:
    1. Exact filename match in any subdirectory
    2. Partial match — identifier appears anywhere in filename
    """
    if not REPORTS_DIR.exists():
        return None, None

    matches = sorted(REPORTS_DIR.rglob(f"*{identifier}*.json"))
    if matches:
        json_path = matches[-1]  # Most recent
        txt_path = json_path.with_suffix(".txt")
        return json_path, txt_path if txt_path.exists() else None

    return None, None


@router.get("/reports")
async def list_reports(
    limit: int = 50, month: Optional[str] = None, year: Optional[int] = None
):
    """List all available reports, most recent first.

    Optional filters:
        year: Filter by year (e.g. 2026)
        month: Filter by month number or name (e.g. "01", "January", "01-January")
    """
    if not REPORTS_DIR.exists():
        return {"count": 0, "reports": []}

    if year or month:
        search_dir = REPORTS_DIR
        if year:
            search_dir = search_dir / str(year)
        if month:
            month_dirs = (
                sorted(search_dir.glob(f"*{month}*")) if search_dir.exists() else []
            )
            if month_dirs:
                search_dir = month_dirs[0]
        json_files = (
            sorted(search_dir.rglob("*.json"), reverse=True)
            if search_dir.exists()
            else []
        )
    else:
        json_files = sorted(REPORTS_DIR.rglob("*.json"), reverse=True)

    reports = []
    for f in json_files[:limit]:
        rel_path = f.relative_to(REPORTS_DIR)
        reports.append(
            {
                "filename": f.stem,
                "path": str(rel_path),
                "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "size_bytes": f.stat().st_size,
            }
        )
    return {"count": len(reports), "reports": reports}


@router.get("/reports/{identifier}")
async def get_report(identifier: str):
    """Retrieve handoff report for a completed triage session.

    Accepts: full filename stem, legacy UUID, partial patient name, or short ID.
    """
    try:
        json_path, txt_path = _find_report_files(identifier)

        if json_path is None or not json_path.exists():
            raise HTTPException(
                status_code=404, detail=f"Report not found for '{identifier}'"
            )

        with open(json_path, "r") as f:
            structured_data = json.load(f)

        sbar_text = ""
        if txt_path and txt_path.exists():
            with open(txt_path, "r") as f:
                sbar_text = f.read()

        return {
            "filename": json_path.stem,
            "structured": structured_data,
            "sbar": sbar_text,
            "report_files": {
                "json": str(json_path),
                "txt": str(txt_path) if txt_path else None,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[REPORTS] Error retrieving report for {identifier}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Error retrieving report")


@router.get("/storage-status")
async def storage_status():
    """Diagnostic endpoint: check blob storage configuration and connectivity.

    Returns whether blob storage is configured, connected, and lists recent blobs.
    Does NOT expose the connection string itself.
    """
    from src.config import AZURE_STORAGE_CONNECTION_STRING, AZURE_BLOB_CONTAINER

    result = {
        "blob_configured": bool(AZURE_STORAGE_CONNECTION_STRING),
        "blob_container_name": AZURE_BLOB_CONTAINER,
        "blob_connected": False,
        "blob_error": None,
        "recent_blobs": [],
        "local_report_count": 0,
        "local_reports_dir": str(REPORTS_DIR.resolve()),
    }

    # Count local reports
    if REPORTS_DIR.exists():
        local_jsons = list(REPORTS_DIR.rglob("*.json"))
        result["local_report_count"] = len(local_jsons)
        result["local_reports"] = [
            str(f.relative_to(REPORTS_DIR)) for f in sorted(local_jsons)[-10:]
        ]

    # Test blob connectivity
    if not AZURE_STORAGE_CONNECTION_STRING:
        result["blob_error"] = "AZURE_STORAGE_CONNECTION_STRING not set"
        return result

    try:
        from azure.storage.blob import BlobServiceClient

        client = BlobServiceClient.from_connection_string(
            AZURE_STORAGE_CONNECTION_STRING
        )
        container = client.get_container_client(AZURE_BLOB_CONTAINER)

        if not container.exists():
            result["blob_error"] = f"Container '{AZURE_BLOB_CONTAINER}' does not exist"
            return result

        result["blob_connected"] = True

        # List recent blobs (last 20)
        blobs = []
        for blob in container.list_blobs():
            blobs.append(
                {
                    "name": blob.name,
                    "size": blob.size,
                    "last_modified": blob.last_modified.isoformat()
                    if blob.last_modified
                    else None,
                }
            )
        # Sort by last_modified descending, take last 20
        blobs.sort(key=lambda b: b.get("last_modified") or "", reverse=True)
        result["recent_blobs"] = blobs[:20]
        result["total_blob_count"] = len(blobs)

    except Exception as exc:
        result["blob_error"] = str(exc)

    return result
