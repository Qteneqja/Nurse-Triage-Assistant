from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

from src.security.middleware import add_security_middleware


def _client() -> TestClient:
    app = FastAPI()
    add_security_middleware(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/test")
    async def api_response() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/dashboard")
    async def dashboard_shell() -> PlainTextResponse:
        return PlainTextResponse("dashboard")

    @app.get("/api/v1/voice/audio/typing.wav")
    async def cacheable_audio() -> PlainTextResponse:
        return PlainTextResponse(
            "wav",
            media_type="audio/wav",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    return TestClient(app)


def test_security_headers_are_applied_to_health_response():
    response = _client().get("/health")

    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert "camera=()" in response.headers["permissions-policy"]


def test_sensitive_api_and_dashboard_responses_are_no_store():
    client = _client()

    api_response = client.get("/api/v1/test")
    dashboard_response = client.get("/dashboard")

    assert api_response.headers["cache-control"] == "no-store"
    assert dashboard_response.headers["cache-control"] == "no-store"
    assert api_response.json() == {"ok": True}


def test_cacheable_audio_response_keeps_existing_cache_policy():
    response = _client().get("/api/v1/voice/audio/typing.wav")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=86400"
