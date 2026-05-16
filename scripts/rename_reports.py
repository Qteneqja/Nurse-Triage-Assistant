"""
Migrate reports into year/month folder structure with clean naming.

Handles two cases:
1. Legacy UUID-named reports in reports/ root (e.g. 85bbdf57-...json)
2. Already-renamed reports sitting in reports/ root (e.g. 2026-01-30_100103_...json)

After migration:
    reports/2026/01-January/2026-01-30_100103_Patient-Name_ER-NOW_85bbdf57.json

Usage:
    python -m scripts.rename_reports              # preview changes (dry run)
    python -m scripts.rename_reports --apply      # actually move files
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.report_naming import generate_report_path, ensure_year_folders

REPORTS_DIR = Path("reports")

# Pattern for legacy UUID filenames
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Pattern for already-renamed files: YYYY-MM-DD_HHMMSS_...
DATED_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{6})_")


def _extract_info(json_path: Path) -> tuple[str, str, str]:
    """Extract session_id, patient_name, disposition from a report JSON."""
    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return json_path.stem, "", ""

    # Patient name
    patient = data.get("patient", {})
    name = patient.get("name", "") if isinstance(patient, dict) else ""

    # Disposition
    disp_obj = data.get("disposition", {})
    if isinstance(disp_obj, dict):
        disposition = disp_obj.get("level", "")
    elif isinstance(disp_obj, str):
        disposition = disp_obj
    else:
        disposition = ""

    # Session ID: try to extract from the filename suffix (short ID) or use stem
    session_id = json_path.stem
    return session_id, name, disposition


def _extract_session_id_from_stem(stem: str) -> str:
    """Pull the short UUID suffix from a dated filename, or return the stem."""
    # Dated files end with _<8-char-id>
    parts = stem.rsplit("_", 1)
    if len(parts) == 2 and len(parts[1]) == 8:
        return parts[1]
    return stem


def migrate_reports(apply: bool = False) -> list[tuple[str, str]]:
    """Move reports from reports/ root into year/month subfolders.

    Returns list of (old_path, new_path) tuples (relative to REPORTS_DIR).
    """
    moves: list[tuple[str, str]] = []

    # Only look at files in the reports root (not already in subfolders)
    json_files = sorted(REPORTS_DIR.glob("*.json"))
    for json_path in json_files:
        stem = json_path.stem

        # Determine timestamp and session_id based on filename type
        if UUID_PATTERN.match(stem):
            # Legacy UUID file — use mtime as timestamp, stem as session_id
            mtime = datetime.fromtimestamp(json_path.stat().st_mtime)
            session_id, patient_name, disposition = _extract_info(json_path)
        elif DATED_PATTERN.match(stem):
            # Already-renamed but in root — parse timestamp from filename
            m = DATED_PATTERN.match(stem)
            assert m is not None
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            time_str = m.group(4)
            hour, minute, second = (
                int(time_str[:2]),
                int(time_str[2:4]),
                int(time_str[4:6]),
            )
            mtime = datetime(year, month, day, hour, minute, second)
            session_id = _extract_session_id_from_stem(stem)
            _, patient_name, disposition = _extract_info(json_path)
        else:
            # Unknown format — skip
            continue

        new_base = generate_report_path(
            reports_dir=REPORTS_DIR,
            session_id=session_id,
            patient_name=patient_name,
            disposition=disposition,
            timestamp=mtime,
        )
        new_json = new_base.with_suffix(".json")

        # Skip if destination already exists
        if new_json.exists():
            continue

        old_rel = str(json_path.relative_to(REPORTS_DIR))
        new_rel = str(new_json.relative_to(REPORTS_DIR))
        moves.append((old_rel, new_rel))

        if apply:
            json_path.rename(new_json)

            txt_path = json_path.with_suffix(".txt")
            if txt_path.exists():
                txt_path.rename(new_base.with_suffix(".txt"))

    return moves


def main():
    parser = argparse.ArgumentParser(description="Move reports into year/month folders")
    parser.add_argument(
        "--apply", action="store_true", help="Actually move files (default is dry run)"
    )
    args = parser.parse_args()

    if not REPORTS_DIR.exists():
        print("No reports directory found.")
        return

    # Ensure month folders exist
    ensure_year_folders(REPORTS_DIR)

    moves = migrate_reports(apply=args.apply)

    if not moves:
        print("No reports to migrate (all already in subfolders).")
        return

    mode = "MOVED" if args.apply else "WOULD MOVE"
    print(f"\n{mode} {len(moves)} report(s):\n")
    for old, new in moves:
        print(f"  {old}")
        print(f"    -> {new}")
        print()

    if not args.apply:
        print("This was a dry run. Use --apply to actually move files.")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
