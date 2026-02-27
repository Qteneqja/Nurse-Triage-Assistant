"""
Twilio Signature Validation — Phase 5

Validates the X-Twilio-Signature header on all /api/v1/voice/* webhook
endpoints.  Enabled by default in staging/production; disabled in dev unless
TWILIO_VALIDATE_SIGNATURE=true.

Uses the official twilio.request_validator.RequestValidator which computes
HMAC-SHA1 over (url + sorted POST params) using TWILIO_AUTH_TOKEN as the key.

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
    No-ops silently when validation is disabled (dev mode).
    """
    if not TWILIO_VALIDATE_SIGNATURE:
        return

    if not TWILIO_AUTH_TOKEN:
        logger.error("[TwilioSig] TWILIO_VALIDATE_SIGNATURE=true but no TWILIO_AUTH_TOKEN set")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: Twilio auth token not set.",
        )

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        logger.warning("[TwilioSig] Missing X-Twilio-Signature header")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing Twilio signature.",
        )

    # Reconstruct the URL Twilio used to call us
    if TWILIO_WEBHOOK_BASE_URL:
        # Use configured base URL (behind ngrok/load-balancer)
        url = urljoin(TWILIO_WEBHOOK_BASE_URL.rstrip("/") + "/", request.url.path.lstrip("/"))
    else:
        url = str(request.url).split("?")[0]  # Twilio uses POST, no query string normally

    # Read form body (Twilio sends application/x-www-form-urlencoded)
    try:
        form_data = await request.form()
        params = {k: str(v) for k, v in form_data.items()}
    except Exception:
        params = {}

    expected = _compute_twilio_signature(TWILIO_AUTH_TOKEN, url, params)

    if not hmac.compare_digest(expected, signature):
        logger.warning(f"[TwilioSig] Invalid signature for {request.url.path}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Twilio signature.",
        )
