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

APP_ENV: str = os.getenv("APP_ENV", "development")  # "development" | "staging" | "production"
if APP_ENV not in ("development", "staging", "production"):
    raise RuntimeError(f"APP_ENV must be development|staging|production, got '{APP_ENV}'")


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

REDFLAG_SCORE_THRESHOLD: int = int(float(
    _env_with_deprecation("REDFLAG_SCORE_THRESHOLD", "ESCALATION_SCORE_THRESHOLD", "10")
))
# Backward-compat alias
ESCALATION_SCORE_THRESHOLD: float = float(REDFLAG_SCORE_THRESHOLD)

# ---------------------------------------------------------------------------
# Protocol governance
# ---------------------------------------------------------------------------

PROTOCOL_VERSION: str = os.getenv("PROTOCOL_VERSION", "v1")
# ENVIRONMENT kept for backward compat; prefer APP_ENV going forward
ENVIRONMENT: str = os.getenv("ENVIRONMENT", APP_ENV)  # synced from APP_ENV

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
TRUST_PROXY_HEADERS: bool = os.getenv("TRUST_PROXY_HEADERS", "false").lower() in ("true", "1", "yes")

# CORS — restrict in production; allow all in development
CORS_ALLOWED_ORIGINS: list[str] = [
    o.strip()
    for o in os.getenv("CORS_ALLOWED_ORIGINS", "*" if APP_ENV == "development" else "").split(",")
    if o.strip()
]

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
        errors.append(
            "DATABASE_URL is required when STORAGE_BACKEND=postgres"
        )

    if APP_ENV not in ("development", "staging", "production"):
        errors.append(f"APP_ENV must be development|staging|production, got '{APP_ENV}'")

    # --- Staging + Production shared requirements ---
    if APP_ENV in ("staging", "production"):
        if not DEEPSEEK_API_KEY:
            errors.append("DEEPSEEK_API_KEY is required in staging/production")
        if STORAGE_BACKEND != "postgres":
            errors.append(
                f"{APP_ENV} requires Postgres. Set STORAGE_BACKEND=postgres"
            )
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
            errors.append(
                "Production requires Postgres. Set STORAGE_BACKEND=postgres"
            )
        if not DATABASE_URL:
            errors.append(
                "DATABASE_URL is required in production (STORAGE_BACKEND=postgres)"
            )

    if not (0.0 <= CONFIDENCE_MIN_THRESHOLD <= 1.0):
        errors.append(
            f"CONFIDENCE_MIN_THRESHOLD must be 0.0-1.0, got {CONFIDENCE_MIN_THRESHOLD}"
        )

    if REDFLAG_SCORE_THRESHOLD < 1:
        errors.append(
            f"REDFLAG_SCORE_THRESHOLD must be >= 1, got {REDFLAG_SCORE_THRESHOLD}"
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
    logger.info(f"[CONFIG] TWILIO_VALIDATE_SIGNATURE={TWILIO_VALIDATE_SIGNATURE}")
    logger.info(f"[CONFIG] RUN_MIGRATIONS_ON_STARTUP={RUN_MIGRATIONS_ON_STARTUP}")
