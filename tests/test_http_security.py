import httpx
import pytest
from fastapi import FastAPI

from app.config import settings
from app.security.http_security import (
    RequestOriginGuardMiddleware,
    SecurityHeadersMiddleware,
    normalize_origin,
)


def build_app():
    app = FastAPI()
    app.add_middleware(RequestOriginGuardMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/admin-ui")
    async def admin_ui():
        return {"ok": True}

    @app.post("/admin/change")
    async def admin_change():
        return {"changed": True}

    @app.post("/webhooks/payments/mock")
    async def payment_webhook():
        return {"accepted": True}

    @app.get("/public")
    async def public():
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_cross_site_cookie_request_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_base_url", "https://legal.example")
    app = build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://legal.example",
        cookies={settings.admin_session_cookie: "ambient-session"},
    ) as client:
        response = await client.post(
            "/admin/change",
            headers={
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-site запрос отклонён"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_cross_site_rejection_emits_privacy_preserving_security_event(
    monkeypatch,
):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_base_url", "https://legal.example")
    captured = []

    async def capture_event(**kwargs):
        captured.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.security.http_security.record_security_event_best_effort",
        capture_event,
    )
    app = build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://legal.example",
        cookies={settings.admin_session_cookie: "ambient-session"},
    ) as client:
        response = await client.post(
            "/admin/change",
            headers={
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )

    assert response.status_code == 403
    assert len(captured) == 1
    event = captured[0]
    assert event["action"] == "security.origin_blocked"
    assert event["severity"] == "warning"
    assert event["details"]["reason"] == "cross_site_fetch"
    assert event["details"]["path"] == "/admin/change"
    assert event["details"]["origin_ref"]
    assert "evil.example" not in event["details"]["origin_ref"]
    assert "ambient-session" not in str(event)


@pytest.mark.asyncio
async def test_same_origin_cookie_request_is_allowed(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_base_url", "https://legal.example")
    app = build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://legal.example",
        cookies={settings.admin_session_cookie: "ambient-session"},
    ) as client:
        response = await client.post(
            "/admin/change",
            headers={
                "Origin": "https://legal.example",
                "Sec-Fetch-Site": "same-origin",
            },
        )
    assert response.status_code == 200
    assert response.json() == {"changed": True}


@pytest.mark.asyncio
async def test_missing_origin_is_blocked_for_production_cookie_session(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_base_url", "https://legal.example")
    app = build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://legal.example",
        cookies={settings.admin_session_cookie: "ambient-session"},
    ) as client:
        response = await client.post("/admin/change")
    assert response.status_code == 403
    assert "Origin" in response.json()["detail"]


@pytest.mark.asyncio
async def test_header_authenticated_api_is_not_treated_as_ambient_cookie(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_base_url", "https://legal.example")
    app = build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://legal.example",
    ) as client:
        response = await client.post(
            "/admin/change",
            headers={
                "x-admin-token": "explicit-bearer-token",
                "Origin": "https://automation.example",
            },
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_external_webhook_is_exempt_from_browser_origin_guard(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_base_url", "https://legal.example")
    app = build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://legal.example",
        cookies={settings.admin_session_cookie: "ambient-session"},
    ) as client:
        response = await client.post(
            "/webhooks/payments/mock",
            headers={"Origin": "https://payment-provider.example"},
        )
    assert response.status_code == 200
    assert response.json() == {"accepted": True}


@pytest.mark.asyncio
async def test_security_headers_and_no_store_are_applied(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_base_url", "https://legal.example")
    app = build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://legal.example",
    ) as client:
        sensitive = await client.get("/admin-ui")
        public = await client.get("/public")

    assert sensitive.headers["cache-control"] == "no-store, max-age=0"
    assert sensitive.headers["pragma"] == "no-cache"
    assert sensitive.headers["strict-transport-security"].startswith(
        "max-age=31536000"
    )
    assert sensitive.headers["x-content-type-options"] == "nosniff"
    assert sensitive.headers["referrer-policy"] == "no-referrer"
    assert sensitive.headers["cross-origin-opener-policy"] == "same-origin"
    assert sensitive.headers["cross-origin-resource-policy"] == "same-origin"
    assert "payment=()" in sensitive.headers["permissions-policy"]
    assert "default-src 'self'" in sensitive.headers["content-security-policy"]
    assert public.headers.get("cache-control") != "no-store, max-age=0"


def test_origin_normalization_removes_default_ports_and_paths():
    assert normalize_origin("HTTPS://Legal.Example:443/path?q=1") == (
        "https://legal.example"
    )
    assert normalize_origin("http://legal.example:8080/path") == (
        "http://legal.example:8080"
    )
    assert normalize_origin("null") is None
