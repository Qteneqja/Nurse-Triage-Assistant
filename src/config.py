"""
Centralized Configuration — Phase 5

All environment variables are read here and validated at startup.
Fail-fast on missing required config.

APP_ENV selects the environment profile:
  - development (default): relaxed defaults, mock LLM allowed
  - staging: production-like but may use test credentials
  - production: strict; Postgres, real LLM, Twilio sig validation required
"""

from __future__ import annotations

import os
import logging
import warnings
from typing import Optional

from dotenv import load_dotenv

# Only load .env file if it exists — production uses platform-injected env vars
_env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.isfile(_env_file):
    load_dotenv(_env_file)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment profile (APP_ENV)
# ---------------------------------------------------------------------------

APP_ENV: str = os.getenv(
    "APP_ENV", "development"
)  # "development" | "staging" | "production" | "test"
if APP_ENV not in ("development", "staging", "production", "test"):
    raise RuntimeError(
        f"APP_ENV must be development|staging|production|test, got '{APP_ENV}'"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_with_deprecation(new_name: str, old_name: str, default: str) -> str:
    """Read *new_name* first; fall back to *old_name* with a deprecation warning."""
    val = os.getenv(new_name)
    if val is not None:
        return val
    old_val = os.getenv(old_name)
    if old_val is not None:
        warnings.warn(
            f"Environment variable '{old_name}' is deprecated — "
            f"use '{new_name}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return old_val
    return default


def _env_flag(name: str, default: str) -> bool:
    """Read a boolean environment flag."""

    return os.getenv(name, default).lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Application version (single source of truth: VERSION file at repo root)
# ---------------------------------------------------------------------------

_version_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION")
try:
    with open(_version_file) as _vf:
        APP_VERSION: str = _vf.read().strip()
except FileNotFoundError:
    APP_VERSION = "5.0.0"  # fallback

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "memory")  # "memory" | "postgres"
DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")
STORE_PHI: bool = os.getenv("STORE_PHI", "false").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# LLM (re-exported from existing config for backward compat)
# ---------------------------------------------------------------------------

DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
USE_MOCK_LLM: bool = os.getenv("USE_MOCK_LLM", "false").lower() == "true"
LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# Triage thresholds
#
# CONFIDENCE_MIN_THRESHOLD (float 0-1, default 0.60)
#   The minimum *confidence score* below which the system escalates to a
#   human nurse.  Confidence starts at 1.0 and is reduced by deterministic
#   deductions (missing info, contradictions, unclear answers, etc.).
#   This is the Phase 1 "PHASE1_CONFIDENCE_ESCALATION_THRESHOLD".
#
# REDFLAG_SCORE_THRESHOLD (int, default 10)
#   The weighted integer red-flag score at or above which the pre-check
#   safety gate escalates to URGENT (no LLM call made).  Individual red-
#   flag patterns carry integer weights; their sum is compared to this
#   threshold.
# ---------------------------------------------------------------------------

CONFIDENCE_MIN_THRESHOLD: float = float(
    _env_with_deprecation("CONFIDENCE_MIN_THRESHOLD", "CONFIDENCE_THRESHOLD", "0.60")
)
# Backward-compat alias (read-only, kept for any Phase 2 code that imports it)
CONFIDENCE_THRESHOLD: float = CONFIDENCE_MIN_THRESHOLD

REDFLAG_SCORE_THRESHOLD: int = int(
    float(
        _env_with_deprecation(
            "REDFLAG_SCORE_THRESHOLD", "ESCALATION_SCORE_THRESHOLD", "10"
        )
    )
)
# Backward-compat alias
ESCALATION_SCORE_THRESHOLD: float = float(REDFLAG_SCORE_THRESHOLD)

# ---------------------------------------------------------------------------
# Protocol governance
# ---------------------------------------------------------------------------

PROTOCOL_VERSION: str = os.getenv("PROTOCOL_VERSION", "v1")
# ENVIRONMENT kept for backward compat; prefer APP_ENV going forward
ENVIRONMENT: str = os.getenv("ENVIRONMENT", APP_ENV)  # synced from APP_ENV

# ---------------------------------------------------------------------------
# Platform workflow routing
# ---------------------------------------------------------------------------

DEFAULT_WORKFLOW_ID: str = os.getenv("DEFAULT_WORKFLOW_ID", "healthcare_triage_v1")
DEFAULT_VERTICAL_KEY: str = os.getenv("DEFAULT_VERTICAL_KEY", "healthcare")
DEFAULT_WORKFLOW_VERSION: str = os.getenv("DEFAULT_WORKFLOW_VERSION", "v1")

# Generic fallback flag. The legacy healthcare-named env var remains supported
# for compatibility with Phase 10 deployments.
ENABLE_DEFAULT_WORKFLOW_ROUTE: bool = _env_flag(
    "ENABLE_DEFAULT_WORKFLOW_ROUTE",
    os.getenv(
        "ENABLE_DEFAULT_HEALTHCARE_ROUTE",
        "false" if ENVIRONMENT == "production" else "true",
    ),
)
ENABLE_DEFAULT_HEALTHCARE_ROUTE: bool = ENABLE_DEFAULT_WORKFLOW_ROUTE

# Workflow hints are convenient in local/dev tools but should not be accepted
# in production unless explicitly enabled.
ENABLE_WORKFLOW_HINT_ROUTE: bool = _env_flag(
    "ENABLE_WORKFLOW_HINT_ROUTE",
    "false" if ENVIRONMENT == "production" else "true",
)

# Default route bootstrap inputs.
DEFAULT_ORGANIZATION_NAME: str = os.getenv(
    "DEFAULT_ORGANIZATION_NAME", "Default Healthcare Organization"
)
DEFAULT_ORGANIZATION_SLUG: str = os.getenv(
    "DEFAULT_ORGANIZATION_SLUG", "default-healthcare"
)
DEFAULT_TWILIO_PHONE_NUMBER: str = os.getenv("DEFAULT_TWILIO_PHONE_NUMBER", "")

# Phase 13 insurance FNOL placeholder route. This is a safe local/dev default
# only; production should seed real phone_number rows instead of relying on it.
INSURANCE_FNOL_PHONE_NUMBER: str = os.getenv(
    "INSURANCE_FNOL_PHONE_NUMBER",
    "+15555550130",
)

# Temporary shared-number front door. When enabled, Twilio calls first hear a
# vertical menu and are routed to healthcare, insurance, or automotive
# collision based on the caller's choice. Set
# SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER to scope the menu to one phone
# number, or leave it blank to apply to all inbound numbers while enabled.
ENABLE_SHARED_NUMBER_VERTICAL_MENU: bool = _env_flag(
    "ENABLE_SHARED_NUMBER_VERTICAL_MENU",
    "false",
)
SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER: str = os.getenv(
    "SHARED_NUMBER_VERTICAL_MENU_PHONE_NUMBER",
    "",
)

# Phase 14 Birchwood Collision demo route. This is a fake local/dev placeholder
# for ORCA's automotive collision intake workflow, not a production Birchwood
# phone number.
BIRCHWOOD_COLLISION_PHONE_NUMBER: str = os.getenv(
    "BIRCHWOOD_COLLISION_PHONE_NUMBER",
    "+15555550140",
)

# TODO(stakeholder-confirmation): Confirm exact Birchwood collision center
# location names/addresses and the glass department transfer process before
# production use. Demo values intentionally remain placeholders.
BIRCHWOOD_COLLISION_LOCATION_1: str = os.getenv(
    "BIRCHWOOD_COLLISION_LOCATION_1",
    "BIRCHWOOD_COLLISION_LOCATION_1",
)
BIRCHWOOD_COLLISION_LOCATION_2: str = os.getenv(
    "BIRCHWOOD_COLLISION_LOCATION_2",
    "BIRCHWOOD_COLLISION_LOCATION_2",
)
BIRCHWOOD_COLLISION_LOCATION_3: str = os.getenv(
    "BIRCHWOOD_COLLISION_LOCATION_3",
    "BIRCHWOOD_COLLISION_LOCATION_3",
)
BIRCHWOOD_COLLISION_LUXURY_LOCATION: str = os.getenv(
    "BIRCHWOOD_COLLISION_LUXURY_LOCATION",
    "BIRCHWOOD_COLLISION_LUXURY_LOCATION",
)
BIRCHWOOD_COLLISION_LUXURY_BRANDS: str = os.getenv(
    "BIRCHWOOD_COLLISION_LUXURY_BRANDS",
    "Audi,Porsche,BMW,Mercedes-Benz,Lexus,Land Rover,Jaguar,Genesis,"
    "Acura,Cadillac,Infiniti,Volvo",
)
BIRCHWOOD_SHORT_FIELD_TIMEOUT_SECONDS: int = int(
    os.getenv("BIRCHWOOD_SHORT_FIELD_TIMEOUT_SECONDS", "5")
)
BIRCHWOOD_NARRATIVE_TIMEOUT_SECONDS: int = int(
    os.getenv("BIRCHWOOD_NARRATIVE_TIMEOUT_SECONDS", "15")
)
BIRCHWOOD_NARRATIVE_SPEECH_TIMEOUT_SECONDS: str = os.getenv(
    "BIRCHWOOD_NARRATIVE_SPEECH_TIMEOUT_SECONDS",
    "auto",
)
BIRCHWOOD_ALLOW_LONG_INCIDENT_DESCRIPTION: bool = _env_flag(
    "BIRCHWOOD_ALLOW_LONG_INCIDENT_DESCRIPTION",
    "true",
)
BIRCHWOOD_AZURE_TTS_VOICE: str = os.getenv(
    "BIRCHWOOD_AZURE_TTS_VOICE",
    "en-CA-ClaraNeural",
)
BIRCHWOOD_AZURE_TTS_RATE: str = os.getenv(
    "BIRCHWOOD_AZURE_TTS_RATE",
    "+3%",
)
BIRCHWOOD_AZURE_TTS_PITCH: str = os.getenv(
    "BIRCHWOOD_AZURE_TTS_PITCH",
    "+0%",
)
BIRCHWOOD_AZURE_TTS_STYLE: str = os.getenv(
    "BIRCHWOOD_AZURE_TTS_STYLE",
    "friendly",
)
BIRCHWOOD_AZURE_TTS_BREAK_MS: int = int(
    os.getenv("BIRCHWOOD_AZURE_TTS_BREAK_MS", "250")
)

# ---------------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------------

TWILIO_WEBHOOK_BASE_URL: str = os.getenv("TWILIO_WEBHOOK_BASE_URL", "")
TWILIO_AUTH_TOKEN: Optional[str] = os.getenv("TWILIO_AUTH_TOKEN")
# Twilio signature validation: enabled by default in staging/production.
# Set TWILIO_VALIDATE_SIGNATURE=false explicitly to disable in dev.
TWILIO_VALIDATE_SIGNATURE: bool = os.getenv(
    "TWILIO_VALIDATE_SIGNATURE",
    "false" if APP_ENV == "development" else "true",
).lower() in ("true", "1", "yes")
# Phone number to transfer the caller to at the end of a successful triage
# (e.g. "+18005551234").  When set, the TwiML for a finalized call uses
# <Dial> instead of <Hangup> so the caller is warm-transferred to the nurse
# queue without hearing dead air.  Leave empty (default) to hang up.
NURSE_TRANSFER_NUMBER: str = os.getenv("NURSE_TRANSFER_NUMBER", "")

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

RATE_LIMIT: str = os.getenv("RATE_LIMIT", "60/minute")
LOG_FORMAT: str = os.getenv("LOG_FORMAT", "json")  # "json" | "text"
TRUST_PROXY_HEADERS: bool = os.getenv("TRUST_PROXY_HEADERS", "false").lower() in (
    "true",
    "1",
    "yes",
)

# Dashboard/admin shell. Local development is allowed without a token, while
# staging/production API access requires DASHBOARD_ADMIN_TOKEN.
DASHBOARD_ENABLED: bool = _env_flag("DASHBOARD_ENABLED", "true")
DASHBOARD_ADMIN_TOKEN: str = os.getenv("DASHBOARD_ADMIN_TOKEN") or os.getenv(
    "ADMIN_API_KEY", ""
)

# CORS — restrict in production; allow all in development
CORS_ALLOWED_ORIGINS: list[str] = [
    o.strip()
    for o in os.getenv(
        "CORS_ALLOWED_ORIGINS", "*" if APP_ENV == "development" else ""
    ).split(",")
    if o.strip()
]

# Azure Blob Storage (persistent report storage in production)
AZURE_STORAGE_CONNECTION_STRING: Optional[str] = os.getenv(
    "AZURE_STORAGE_CONNECTION_STRING"
)
AZURE_BLOB_CONTAINER: str = os.getenv("AZURE_BLOB_CONTAINER", "triage-reports")

# Azure Speech Service (TTS)
AZURE_SPEECH_KEY: Optional[str] = os.getenv("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION: str = os.getenv("AZURE_SPEECH_REGION", "eastus")
AZURE_TTS_VOICE: str = os.getenv("AZURE_TTS_VOICE", "en-US-AvaMultilingualNeural")

# Database migrations on startup (safe + idempotent via Alembic upgrade head)
RUN_MIGRATIONS_ON_STARTUP: bool = os.getenv(
    "RUN_MIGRATIONS_ON_STARTUP", "false"
).lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))


# ---------------------------------------------------------------------------
# Startup Validation
# ---------------------------------------------------------------------------


def validate_config() -> list[str]:
    """Validate configuration at startup.

    Returns list of error messages. Empty list means all OK.
    """
    errors: list[str] = []

    if STORAGE_BACKEND not in ("memory", "postgres"):
        errors.append(
            f"STORAGE_BACKEND must be 'memory' or 'postgres', got '{STORAGE_BACKEND}'"
        )

    if STORAGE_BACKEND == "postgres" and not DATABASE_URL:
        errors.append("DATABASE_URL is required when STORAGE_BACKEND=postgres")

    if APP_ENV not in ("development", "staging", "production", "test"):
        errors.append(
            f"APP_ENV must be development|staging|production|test, got '{APP_ENV}'"
        )

    # --- Staging + Production shared requirements ---
    if APP_ENV in ("staging", "production"):
        if not DEEPSEEK_API_KEY:
            errors.append("DEEPSEEK_API_KEY is required in staging/production")
        if STORAGE_BACKEND != "postgres":
            errors.append(f"{APP_ENV} requires Postgres. Set STORAGE_BACKEND=postgres")
        if TWILIO_VALIDATE_SIGNATURE and not TWILIO_AUTH_TOKEN:
            errors.append(
                "TWILIO_AUTH_TOKEN is required when TWILIO_VALIDATE_SIGNATURE=true "
                f"(default in {APP_ENV})"
            )

    # --- Production-only requirements ---
    if APP_ENV == "production" or ENVIRONMENT == "production":
        if not DEEPSEEK_API_KEY:
            errors.append("DEEPSEEK_API_KEY is required in production")
        if STORAGE_BACKEND != "postgres":
            errors.append("Production requires Postgres. Set STORAGE_BACKEND=postgres")
        if not DATABASE_URL:
            errors.append(
                "DATABASE_URL is required in production (STORAGE_BACKEND=postgres)"
            )
        if ENABLE_WORKFLOW_HINT_ROUTE:
            errors.append(
                "ENABLE_WORKFLOW_HINT_ROUTE must be false in production unless "
                "a specific operational exception is approved."
            )

    if not (0.0 <= CONFIDENCE_MIN_THRESHOLD <= 1.0):
        errors.append(
            f"CONFIDENCE_MIN_THRESHOLD must be 0.0-1.0, got {CONFIDENCE_MIN_THRESHOLD}"
        )

    if REDFLAG_SCORE_THRESHOLD < 1:
        errors.append(
            f"REDFLAG_SCORE_THRESHOLD must be >= 1, got {REDFLAG_SCORE_THRESHOLD}"
        )

    if ENABLE_DEFAULT_WORKFLOW_ROUTE:
        if not DEFAULT_WORKFLOW_ID:
            errors.append(
                "DEFAULT_WORKFLOW_ID is required when default routing is enabled"
            )
        if not DEFAULT_VERTICAL_KEY:
            errors.append(
                "DEFAULT_VERTICAL_KEY is required when default routing is enabled"
            )
        if not DEFAULT_WORKFLOW_VERSION:
            errors.append(
                "DEFAULT_WORKFLOW_VERSION is required when default routing is enabled"
            )

    return errors


def require_valid_config() -> None:
    """Validate config and raise on errors. Call at startup."""
    errors = validate_config()
    if errors:
        for err in errors:
            logger.error(f"[CONFIG] {err}")
        raise RuntimeError(
            f"Configuration validation failed with {len(errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    logger.info("[CONFIG] Configuration validated successfully")
    logger.info(f"[CONFIG] APP_ENV={APP_ENV}")
    logger.info(f"[CONFIG] STORAGE_BACKEND={STORAGE_BACKEND}")
    logger.info(f"[CONFIG] STORE_PHI={STORE_PHI}")
    logger.info(f"[CONFIG] ENVIRONMENT={ENVIRONMENT}")
    logger.info(f"[CONFIG] PROTOCOL_VERSION={PROTOCOL_VERSION}")
    logger.info(f"[CONFIG] DEFAULT_VERTICAL_KEY={DEFAULT_VERTICAL_KEY}")
    logger.info(f"[CONFIG] DEFAULT_WORKFLOW_ID={DEFAULT_WORKFLOW_ID}")
    logger.info(f"[CONFIG] DEFAULT_WORKFLOW_VERSION={DEFAULT_WORKFLOW_VERSION}")
    logger.info(
        f"[CONFIG] ENABLE_DEFAULT_WORKFLOW_ROUTE={ENABLE_DEFAULT_WORKFLOW_ROUTE}"
    )
    logger.info(f"[CONFIG] ENABLE_WORKFLOW_HINT_ROUTE={ENABLE_WORKFLOW_HINT_ROUTE}")
    logger.info(
        "[CONFIG] ENABLE_SHARED_NUMBER_VERTICAL_MENU="
        f"{ENABLE_SHARED_NUMBER_VERTICAL_MENU}"
    )
    logger.info(f"[CONFIG] TWILIO_VALIDATE_SIGNATURE={TWILIO_VALIDATE_SIGNATURE}")
    logger.info(f"[CONFIG] RUN_MIGRATIONS_ON_STARTUP={RUN_MIGRATIONS_ON_STARTUP}")
