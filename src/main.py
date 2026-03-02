"""Main FastAPI Application — Phase 5 SaaS Infrastructure"""
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.routes import router as intake_router
from src.twilio.routes import router as twilio_router
from src.storage.factory import get_storage_backend
from src.config import (
    APP_ENV,
    STORAGE_BACKEND,
    ENVIRONMENT,
    LOG_FORMAT,
    PROTOCOL_VERSION,
    CORS_ALLOWED_ORIGINS,
    RUN_MIGRATIONS_ON_STARTUP,
    require_valid_config,
)
from src.observability.logging import (
    configure_structured_logging,
    set_log_context,
    clear_log_context,
    _request_id,
    _call_sid,
)
from src.observability.sentry_integration import init_sentry
from src.observability.metrics import get_metrics
from src.security.middleware import add_security_middleware
from src.governance.protocol_status import validate_approved_protocols_exist
from src.safety.phi_masking import PHIMaskingFilter

# Configure structured logging early
configure_structured_logging(log_format=LOG_FORMAT, level=logging.INFO)

# Install PHI masking filter on ALL log output
logging.getLogger().addFilter(PHIMaskingFilter())

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Correlation-ID / Request-ID middleware
# ---------------------------------------------------------------------------

class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """Inject a unique request ID into every request and propagate to logs."""

    async def dispatch(self, request: Request, call_next):
        # Accept caller-supplied ID or generate one
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        # Extract Twilio CallSid from form body or query for correlation
        call_sid = request.query_params.get("CallSid")

        token_req = _request_id.set(req_id)
        token_sid = _call_sid.set(call_sid)
        try:
            response: Response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            return response
        finally:
            _request_id.reset(token_req)
            _call_sid.reset(token_sid)
            clear_log_context()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Sentry error monitoring (no-op if SENTRY_DSN is unset)
    init_sentry()

    # Startup validation
    require_valid_config()

    # Optional: run Alembic migrations on startup (idempotent)
    if RUN_MIGRATIONS_ON_STARTUP and STORAGE_BACKEND == "postgres":
        logger.info("[STARTUP] Running Alembic migrations (upgrade head)...")
        try:
            from alembic.config import Config as AlembicConfig
            from alembic import command as alembic_command

            alembic_cfg = AlembicConfig("alembic.ini")
            alembic_command.upgrade(alembic_cfg, "head")
            logger.info("[STARTUP] Migrations complete.")
        except Exception as exc:
            logger.error(f"[STARTUP] Migration failed: {exc}", exc_info=True)
            if APP_ENV == "production":
                raise

    # Governance — validate approved protocols exist
    protocol_dir = Path(__file__).resolve().parent.parent / "protocols" / PROTOCOL_VERSION
    try:
        validate_approved_protocols_exist(protocol_dir)
    except RuntimeError as e:
        logger.error(f"[STARTUP] Protocol governance check failed: {e}")
        if ENVIRONMENT == "production":
            raise

    logger.info("Starting...")

    # Pre-warm Azure TTS so the first call doesn't cold-start
    try:
        from src.utils.azure_tts import warm_up as _tts_warm_up
        await _tts_warm_up()
    except Exception as exc:
        logger.warning(f"[STARTUP] TTS warm-up failed (non-fatal): {exc}")

    # Initialize storage backend (factory enforces Postgres in production)
    storage_backend = get_storage_backend()
    logger.info(f"[STARTUP] Storage backend: {STORAGE_BACKEND}")
    logger.info(f"[STARTUP] APP_ENV={APP_ENV}")

    yield
    logger.info("Shutting down...")


app = FastAPI(title="Triage API", version="5.0.0", lifespan=lifespan)

# Phase 5: Correlation-ID middleware (outermost)
app.add_middleware(CorrelationIDMiddleware)

# Phase 3: Security middleware (rate-limiting + safe errors)
add_security_middleware(app)

# CORS — restricted in staging/production, wide-open in dev
if CORS_ALLOWED_ORIGINS and CORS_ALLOWED_ORIGINS != ["*"]:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
elif APP_ENV == "development":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # staging/prod with no explicit origins — no CORS headers added
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=[],
    )

app.include_router(intake_router, prefix="/api/v1/intake", tags=["Intake"])
app.include_router(twilio_router, prefix="/api/v1/voice", tags=["Twilio Voice"])


@app.get("/")
async def root():
    return {"service": "Triage API", "status": "operational", "version": "5.0.0"}


@app.get("/health")
async def health_check():
    """Liveness probe — confirms the process is up. No external deps.

    No authentication required. No database call. No Twilio signature validation.
    Purpose: load balancer / container orchestrator liveness probe.
    Updated: Production Hardening Step 2.
    """
    from datetime import datetime, timezone
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/voice/audio/typing.wav")
async def serve_typing_sound():
    """Serve the keyboard-typing hold sound (no auth — fetched by Twilio <Play>)."""
    from src.utils.typing_sound import get_typing_sound_wav
    return Response(
        content=get_typing_sound_wav(),
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/ready")
async def readiness_check():
    """Readiness probe — checks DB connectivity when postgres enabled.

    No authentication required. No Twilio signature validation.
    Purpose: readiness probe — tells orchestrator when the app can accept traffic.
    In production, does NOT leak exception messages or stack traces.
    Updated: Production Hardening Step 2.
    """
    checks: dict = {"storage": STORAGE_BACKEND}

    if STORAGE_BACKEND == "postgres":
        try:
            backend = get_storage_backend()
            if hasattr(backend, "check_connectivity"):
                db_ok = backend.check_connectivity()
                checks["database"] = "connected" if db_ok else "unreachable"
                if not db_ok:
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        status_code=503,
                        content={"status": "not_ready", "database": "unavailable"},
                    )
            else:
                checks["database"] = "not_applicable"
        except Exception as exc:
            from fastapi.responses import JSONResponse
            # In production: do NOT include exception messages, stack traces,
            # or any internal details (HIPAA / security best practice).
            if APP_ENV == "production":
                return JSONResponse(
                    status_code=503,
                    content={"status": "not_ready", "database": "unavailable"},
                )
            else:
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "not_ready",
                        "database": "unavailable",
                        "detail": str(exc),
                    },
                )

    return {"status": "ready", **checks}


@app.get("/metrics")
async def metrics_endpoint():
    """Expose application metrics (Phase 3)."""
    return get_metrics().to_dict()