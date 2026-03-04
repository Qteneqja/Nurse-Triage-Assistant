"""
Twilio Signature Validation — Phase 5 (Updated: Production Hardening Step 2)

Validates the X-Twilio-Signature header on all /api/v1/voice/* webhook
endpoints.  Enabled by default in staging/production; disabled in dev unless
TWILIO_VALIDATE_SIGNATURE=true.

Uses a manual HMAC-SHA1 computation matching the official Twilio
RequestValidator algorithm over (url + sorted POST params) using
TWILIO_AUTH_TOKEN as the key.

If behind a reverse proxy, set TWILIO_WEBHOOK_BASE_URL to the public URL;
otherwise request.url is used automatically.

Because FastAPI/Starlette already parses the body before middleware can
access it in BaseHTTPMiddleware, we implement this as a FastAPI dependency
injected into the Twilio router.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from base64 import b64encode
from urllib.parse import urljoin

from fastapi import Request, HTTPException, status

from src.config import (
    APP_ENV,
    TWILIO_AUTH_TOKEN,
    TWILIO_VALIDATE_SIGNATURE,
    TWILIO_WEBHOOK_BASE_URL,
)

logger = logging.getLogger(__name__)


def _compute_twilio_signature(auth_token: str, uri: str, params: dict[str, str]) -> str:
    """Compute the Twilio X-Twilio-Signature value.

    Algorithm (per Twilio docs):
    1. Take the full URL of the request.
    2. Sort the POST parameters alphabetically by key.
    3. Append each key-value pair to the URL (no separators).
    4. HMAC-SHA1 the result with auth_token as the key.
    5. Base64-encode the HMAC digest.
    """
    s = uri
    if params:
        for key in sorted(params.keys()):
            s += key + (params[key] or "")
    mac = hmac.new(auth_token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1)
    return b64encode(mac.digest()).decode("utf-8")


async def validate_twilio_signature(request: Request) -> None:
    """FastAPI dependency that validates the Twilio request signature.

    Raises 403 if validation is enabled and the signature is missing/invalid.
    Logs a warning when validation is skipped in non-production environments.
    """
    if not TWILIO_VALIDATE_SIGNATURE:
        logger.warning(
            "[TwilioSig] Signature validation SKIPPED — "
            "TWILIO_VALIDATE_SIGNATURE=false (APP_ENV=%s). "
            "This is acceptable in dev/test but must be enabled in production.",
            APP_ENV,
        )
        return

    if not TWILIO_AUTH_TOKEN:
        logger.error(
            "[TwilioSig] TWILIO_VALIDATE_SIGNATURE=true but no TWILIO_AUTH_TOKEN set"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: Twilio auth token not set.",
        )

    # Extract source IP for security logging
    source_ip = request.client.host if request.client else "unknown"

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        logger.warning(
            "[TwilioSig] SECURITY: Missing X-Twilio-Signature header — "
            "source_ip=%s endpoint=%s reason=missing_signature",
            source_ip,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Invalid Twilio signature"},
        )

    # Reconstruct the URL Twilio used to call us
    # If behind reverse proxy, set TWILIO_WEBHOOK_BASE_URL to the public URL;
    # otherwise request.url is used automatically.
    if TWILIO_WEBHOOK_BASE_URL:
        url = urljoin(
            TWILIO_WEBHOOK_BASE_URL.rstrip("/") + "/", request.url.path.lstrip("/")
        )
    else:
        url = str(request.url).split("?")[
            0
        ]  # Twilio uses POST, no query string normally

    # Log URL used for signature validation at DEBUG level (no query params or body)
    logger.debug("[TwilioSig] Validating signature against URL: %s", url)

    # Read form body (Twilio sends application/x-www-form-urlencoded)
    try:
        form_data = await request.form()
        params = {k: str(v) for k, v in form_data.items()}
    except Exception:
        params = {}

    expected = _compute_twilio_signature(TWILIO_AUTH_TOKEN, url, params)

    if not hmac.compare_digest(expected, signature):
        logger.warning(
            "[TwilioSig] SECURITY: Invalid signature — "
            "source_ip=%s endpoint=%s reason=signature_mismatch",
            source_ip,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Invalid Twilio signature"},
        )
