"""
Protocol Governance — Phase 3

Validates protocol schema governance fields and gates loading
based on protocol status and environment mode.

Extended protocol fields:
- status: draft | approved | deprecated
- effective_date: ISO date string
- reviewed_by: reviewer name
- reviewed_at: ISO datetime
- owner: protocol owner / department

Enforcement:
- Production mode: only "approved" protocols loaded
- Development mode: all protocols loaded (with warnings for non-approved)
- Startup validation: fail if no approved protocols in production
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from src.config import ENVIRONMENT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Governance schema validation
# ---------------------------------------------------------------------------

REQUIRED_PROTOCOL_FIELDS = {"id", "title", "keywords", "body", "version"}
GOVERNANCE_FIELDS = {"status", "effective_date", "reviewed_by", "reviewed_at", "owner"}
VALID_STATUSES = {"draft", "approved", "deprecated"}


def validate_protocol_schema(data: dict, filepath: str = "") -> list[str]:
    """Validate a protocol JSON against governance schema.

    Returns list of error/warning strings.
    """
    issues: list[str] = []

    # Check required base fields
    for field in REQUIRED_PROTOCOL_FIELDS:
        if field not in data:
            issues.append(f"[{filepath}] Missing required field: {field}")

    # Check governance fields
    status = data.get("status")
    if status is not None and status not in VALID_STATUSES:
        issues.append(
            f"[{filepath}] Invalid status '{status}'. "
            f"Must be one of: {VALID_STATUSES}"
        )

    effective_date = data.get("effective_date")
    if effective_date is not None:
        try:
            datetime.fromisoformat(effective_date)
        except (ValueError, TypeError):
            issues.append(
                f"[{filepath}] Invalid effective_date format: {effective_date}"
            )

    reviewed_at = data.get("reviewed_at")
    if reviewed_at is not None:
        try:
            datetime.fromisoformat(reviewed_at)
        except (ValueError, TypeError):
            issues.append(
                f"[{filepath}] Invalid reviewed_at format: {reviewed_at}"
            )

    return issues


def filter_protocols_by_governance(
    protocol_data_list: list[dict],
    environment: str | None = None,
) -> list[dict]:
    """Filter protocol data based on governance status.

    In production: only return approved protocols.
    In development: return all, log warnings for non-approved.

    Args:
        protocol_data_list: Raw protocol dicts loaded from JSON files.
        environment: Override for ENVIRONMENT config.

    Returns:
        Filtered list of protocol dicts.
    """
    env = environment or ENVIRONMENT

    if env == "production":
        approved = [
            p for p in protocol_data_list
            if p.get("status", "approved") == "approved"
        ]
        non_approved = len(protocol_data_list) - len(approved)
        if non_approved > 0:
            logger.warning(
                f"[Governance] Filtered out {non_approved} non-approved "
                f"protocols in production mode"
            )
        return approved
    else:
        # Development: load all, warn about non-approved
        for p in protocol_data_list:
            status = p.get("status", "approved")
            if status != "approved":
                logger.warning(
                    f"[Governance] Protocol '{p.get('id', '?')}' has "
                    f"status='{status}' (non-approved, loaded in dev mode)"
                )
        return protocol_data_list


def validate_approved_protocols_exist(
    protocol_dir: Path,
    environment: str | None = None,
) -> bool:
    """Validate that at least one approved protocol exists.

    In production, raises RuntimeError if none found.

    Returns True if validation passes.
    """
    env = environment or ENVIRONMENT

    if not protocol_dir.is_dir():
        if env == "production":
            raise RuntimeError(
                f"Protocol directory not found: {protocol_dir}. "
                "At least one approved protocol is required in production."
            )
        return False

    approved_count = 0
    for fpath in sorted(protocol_dir.glob("*.json")):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            status = data.get("status", "approved")
            if status == "approved":
                approved_count += 1
        except Exception as exc:
            logger.warning(f"Failed to read protocol {fpath}: {exc}")

    if env == "production" and approved_count == 0:
        raise RuntimeError(
            f"No approved protocols found in {protocol_dir}. "
            "At least one approved protocol is required in production."
        )

    logger.info(f"[Governance] Found {approved_count} approved protocols")
    return approved_count > 0
