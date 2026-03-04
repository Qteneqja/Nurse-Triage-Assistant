"""
Security Middleware — Phase 3

Rate limiting and safe error handling for production deployment.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
import traceback
from collections import defaultdict
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import ENVIRONMENT, RATE_LIMIT, TRUST_PROXY_HEADERS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client IP extraction
# ---------------------------------------------------------------------------


def _extract_client_ip(request: Request) -> str:
    """Return the best-effort client IP address.

    When TRUST_PROXY_HEADERS is True, reads X-Forwarded-For and picks the
    first *public* IP (ignoring private/loopback addresses injected by
    internal proxies).  Falls back to request.client.host.
    """
    if TRUST_PROXY_HEADERS:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            for raw_ip in xff.split(","):
                ip_str = raw_ip.strip()
                try:
                    addr = ipaddress.ip_address(ip_str)
                    if addr.is_global:
                        return ip_str
                except ValueError:
                    continue
            # No public IP found – use the first entry anyway
            return xff.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Thread-safe in-memory rate limiter
# ---------------------------------------------------------------------------


class _RateLimitStore:
    """Thread-safe sliding-window rate limiter per client IP."""

    def __init__(self, rate_limit_str: str = "60/minute") -> None:
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._max_requests, self._window_seconds = self._parse_rate(rate_limit_str)
        self._lock = threading.Lock()

    @staticmethod
    def _parse_rate(rate_str: str) -> tuple[int, int]:
        """Parse rate string like '60/minute' into (max_requests, window_seconds)."""
        parts = rate_str.split("/")
        if len(parts) != 2:
            return 60, 60  # Default fallback

        try:
            max_req = int(parts[0])
        except ValueError:
            max_req = 60

        window_map = {
            "second": 1,
            "minute": 60,
            "hour": 3600,
            "day": 86400,
        }
        window = window_map.get(parts[1].strip().lower(), 60)
        return max_req, window

    def is_allowed(self, client_ip: str) -> bool:
        """Check if a request from client_ip is allowed (thread-safe)."""
        now = time.time()
        cutoff = now - self._window_seconds

        with self._lock:
            # Clean old entries
            self._requests[client_ip] = [
                t for t in self._requests[client_ip] if t > cutoff
            ]

            if len(self._requests[client_ip]) >= self._max_requests:
                return False

            self._requests[client_ip].append(now)
            return True


# Module-level rate limiter
_rate_limiter = _RateLimitStore(RATE_LIMIT)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware based on client IP."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for health endpoints
        if request.url.path in ("/health", "/ready", "/metrics"):
            return await call_next(request)

        client_ip = _extract_client_ip(request)

        if not _rate_limiter.is_allowed(client_ip):
            logger.warning(f"[Security] Rate limit exceeded for {client_ip}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please try again later."},
            )

        return await call_next(request)


class SafeErrorMiddleware(BaseHTTPMiddleware):
    """Catch unhandled exceptions and return safe error responses.

    In production: no stack traces leaked.
    In development: include error details.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            logger.error(
                f"[Security] Unhandled exception on {request.method} {request.url.path}: {exc}",
                exc_info=True,
            )

            if ENVIRONMENT == "production":
                return JSONResponse(
                    status_code=500,
                    content={
                        "detail": "An internal error occurred. Please try again later."
                    },
                )
            else:
                # Dev/staging: include error details but mask any PHI
                from src.safety.phi_masking import mask_phi

                return JSONResponse(
                    status_code=500,
                    content={
                        "detail": mask_phi(str(exc)),
                        "traceback": mask_phi(traceback.format_exc()),
                    },
                )


def add_security_middleware(app: FastAPI) -> None:
    """Add all security middleware to the FastAPI app."""
    app.add_middleware(SafeErrorMiddleware)
    app.add_middleware(RateLimitMiddleware)
