"""Production code must not import from scripts/.

The Docker image ships src/ (plus protocols/ and alembic/) but NOT
scripts/ — an import of scripts.* inside src/ works locally and in CI
but raises ModuleNotFoundError in the deployed container (this broke the
enrichment /insights endpoint on staging).
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

_SCRIPTS_IMPORT = re.compile(r"^\s*(from\s+scripts[.\s]|import\s+scripts\b)", re.M)


def test_src_never_imports_scripts():
    offenders = [
        str(path.relative_to(SRC.parent))
        for path in sorted(SRC.rglob("*.py"))
        if _SCRIPTS_IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        f"src/ files import from scripts/, which is not in the Docker image: "
        f"{offenders}. Move the shared code into src/ instead."
    )
