"""
Azure Blob Storage — Report Upload Utility

Uploads triage reports (JSON + SBAR text) to Azure Blob Storage
for persistent, durable storage across container restarts.

Blob path mirrors local report structure:
  triage-reports/2026/02-February/2026-02-28_143256_John-Smith_ER-NOW_a5f4b1b6.json
  triage-reports/2026/02-February/2026-02-28_143256_John-Smith_ER-NOW_a5f4b1b6.txt

Falls back gracefully if Azure Storage is not configured (local-only mode).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-loaded client to avoid import errors when azure-storage-blob
# is not installed (e.g. local dev without Azure deps).
_blob_service_client = None
_container_client = None
_initialized = False


def _get_container_client():
    """Lazy-initialize the Azure Blob container client."""
    global _blob_service_client, _container_client, _initialized

    if _initialized:
        return _container_client

    _initialized = True

    from src.config import AZURE_STORAGE_CONNECTION_STRING, AZURE_BLOB_CONTAINER

    if not AZURE_STORAGE_CONNECTION_STRING:
        logger.info(
            "[BlobStorage] AZURE_STORAGE_CONNECTION_STRING not set — blob upload disabled"
        )
        return None

    try:
        from azure.storage.blob import BlobServiceClient

        _blob_service_client = BlobServiceClient.from_connection_string(
            AZURE_STORAGE_CONNECTION_STRING
        )
        _container_client = _blob_service_client.get_container_client(
            AZURE_BLOB_CONTAINER
        )

        # Create container if it doesn't exist
        if not _container_client.exists():
            _container_client.create_container()
            logger.info(f"[BlobStorage] Created container '{AZURE_BLOB_CONTAINER}'")

        logger.info(f"[BlobStorage] Connected to container '{AZURE_BLOB_CONTAINER}'")
        return _container_client

    except Exception as exc:
        logger.error(f"[BlobStorage] Failed to initialize: {exc}", exc_info=True)
        _container_client = None
        return None


def upload_report_to_blob(
    local_path: Path,
    reports_dir: Path,
    content_type: Optional[str] = None,
) -> Optional[str]:
    """Upload a local report file to Azure Blob Storage.

    Args:
        local_path: Full local path to the report file.
        reports_dir: Base reports directory (to compute relative blob path).
        content_type: MIME type (auto-detected if None).

    Returns:
        Blob URL if uploaded successfully, None if skipped/failed.
    """
    container = _get_container_client()
    if container is None:
        return None

    # Compute blob name from relative path: reports/2026/02-Feb/file.json → 2026/02-Feb/file.json
    try:
        relative = local_path.relative_to(reports_dir)
        blob_name = str(relative).replace("\\", "/")  # Normalize Windows paths
    except ValueError:
        blob_name = local_path.name

    # Auto-detect content type
    if content_type is None:
        suffix = local_path.suffix.lower()
        content_type = {
            ".json": "application/json",
            ".txt": "text/plain; charset=utf-8",
        }.get(suffix, "application/octet-stream")

    try:
        from azure.storage.blob import ContentSettings

        with open(local_path, "rb") as f:
            container.upload_blob(
                name=blob_name,
                data=f,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )

        blob_url = f"{container.url}/{blob_name}"
        logger.info(f"[BlobStorage] Uploaded: {blob_name}")
        return blob_url

    except Exception as exc:
        logger.error(
            f"[BlobStorage] Failed to upload {blob_name}: {exc}", exc_info=True
        )
        return None


def upload_reports_to_blob(
    json_path: Path,
    txt_path: Path,
    reports_dir: Path,
) -> dict[str, Optional[str]]:
    """Upload both JSON and SBAR text reports to blob storage.

    Returns dict with 'json_url' and 'txt_url' keys (None if upload failed).
    """
    return {
        "json_url": upload_report_to_blob(json_path, reports_dir, "application/json"),
        "txt_url": upload_report_to_blob(
            txt_path, reports_dir, "text/plain; charset=utf-8"
        ),
    }
