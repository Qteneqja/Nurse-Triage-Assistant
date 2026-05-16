"""Bootstrap the default organization/vertical/workflow/phone route.

Run after Alembic migrations:
    python scripts/bootstrap_default_route.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DATABASE_URL  # noqa: E402
from src.platform.organizations.bootstrap import (  # noqa: E402
    DefaultRouteBootstrapSettings,
    bootstrap_default_route,
)


def main() -> int:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required to bootstrap the default route")

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    result = bootstrap_default_route(
        session_factory=session_factory,
        settings=DefaultRouteBootstrapSettings.from_environment(),
    )
    print(json.dumps(result.model_dump(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
