"""
Report Naming Utility

Generates human-readable, chronologically sortable report filenames
organized into year/month folders.

Structure: reports/YYYY/MM-Month/YYYY-MM-DD_HHMMSS_Name_DISPOSITION_ShortID
Example:   reports/2026/01-January/2026-01-30_143256_John-Smith_ER-NOW_a5f4b1b6

- Year/month folders keep the directory browsable at scale
- Timestamp prefix ensures natural sort = chronological order within each folder
- Patient name makes files identifiable at a glance
- Disposition gives quick triage context
- Short UUID suffix guarantees uniqueness
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import datetime
from pathlib import Path


def _sanitize_name(name: str, max_len: int = 30) -> str:
    """Sanitize a patient name for use in a filename.

    - Strips accents (e -> e, e with accent -> e)
    - Replaces whitespace/punctuation with hyphens
    - Removes anything that isn't alphanumeric or hyphen
    - Collapses consecutive hyphens
    - Truncates to max_len
    """
    if not name or not name.strip():
        return "Unknown"

    # Normalize unicode and strip accents
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")

    # Replace whitespace and common punctuation with hyphens
    cleaned = re.sub(r"[\s._,;:]+", "-", ascii_name.strip())

    # Remove anything that isn't alphanumeric or hyphen
    cleaned = re.sub(r"[^a-zA-Z0-9\-]", "", cleaned)

    # Collapse consecutive hyphens and strip leading/trailing
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")

    if not cleaned:
        return "Unknown"

    return cleaned[:max_len]


def _sanitize_disposition(disposition: str) -> str:
    """Normalize disposition string for filename use."""
    if not disposition:
        return "PENDING"
    # Already uppercase with underscores from enum — just replace _ with -
    return disposition.upper().replace("_", "-")


def generate_report_filename(
    session_id: str,
    patient_name: str = "",
    disposition: str = "",
    timestamp: datetime | None = None,
) -> str:
    """Generate a sortable, human-readable report filename (without extension).

    Args:
        session_id: UUID session identifier (used for short suffix)
        patient_name: Patient's name (sanitized for filesystem safety)
        disposition: Triage disposition (e.g. "ER_NOW", "SELF_CARE")
        timestamp: Report creation time (defaults to now)

    Returns:
        Filename stem like "2026-01-30_143256_John-Smith_ER-NOW_a5f4b1b6"
    """
    ts = timestamp or datetime.now()
    ts_str = ts.strftime("%Y-%m-%d_%H%M%S")

    name_part = _sanitize_name(patient_name)
    disp_part = _sanitize_disposition(disposition)
    short_id = session_id[:8] if len(session_id) >= 8 else session_id

    return f"{ts_str}_{name_part}_{disp_part}_{short_id}"


def generate_report_path(
    reports_dir: Path,
    session_id: str,
    patient_name: str = "",
    disposition: str = "",
    timestamp: datetime | None = None,
) -> Path:
    """Generate a full report path including year/month subdirectory.

    Returns:
        Path like reports/2026/01-January/2026-01-30_143256_John-Smith_ER-NOW_a5f4b1b6
        (without file extension — caller appends .json / .txt)
    """
    ts = timestamp or datetime.now()
    month_name = calendar.month_name[ts.month]
    subdir = reports_dir / str(ts.year) / f"{ts.month:02d}-{month_name}"
    subdir.mkdir(parents=True, exist_ok=True)

    filename = generate_report_filename(
        session_id=session_id,
        patient_name=patient_name,
        disposition=disposition,
        timestamp=ts,
    )
    return subdir / filename


def ensure_year_folders(reports_dir: Path, year: int | None = None) -> None:
    """Pre-create month folders for the given year (defaults to current year)."""
    yr = year or datetime.now().year
    for month_num in range(1, 13):
        month_name = calendar.month_name[month_num]
        folder = reports_dir / str(yr) / f"{month_num:02d}-{month_name}"
        folder.mkdir(parents=True, exist_ok=True)
