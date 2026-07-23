from __future__ import annotations

from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
SENSITIVE_PREFIXES = (
    "/admin",
    "/auth",
    "/operator",
    "/lawyer",
    "/access",
    "/consultation-slots",
)


def _session_cookie_present(headers: Headers) -> bool:
    raw_cookie = headers.get("cookie")
    if not raw_cookie:
        return False
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie)
    except Exception:
        return False
    return settings.admin_session_cookie in cookie


def _explicit_api_credentials(headers: Headers) -> bool:
    authorization = str(headers.get("authorization") or "").strip().lower()
    admin_token = str(headers.get("x-admin-token") or "").strip()
    return authorization.startswith("bearer ") or bool(admin_token)


def _origin_matches_request(scope: Scope, headers: Headers) -> bool:
    origin = headers.get("origin")
    referer = headers.get("referer")
    source = origin or referer
    if not source:
        return False

    parsed = urlsplit(source)
    host = str(headers.get("host") or "").strip().lower()
    scheme = str(scope.get("scheme") or "http").lower()
    return parsed.scheme.lower() == scheme and parsed.netloc.lower() == host


class HttpSecurityMiddleware:
    """Protect browser sessions and add baseline security headers.

    Cookie-authenticated unsafe requests must originate from the same site.
    API clients using Bearer or X-Admin-Token credentials remain supported.
    The implementation is pure ASGI to avoid BaseHTTPMiddleware buffering and
    context propagation limitations.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        method = str(scope.get("method") or "GET").upper()
        cookie_session = _session_cookie_present(headers)
        explicit_credentials = _explicit_api_credentials(headers)

        if (
            method not in SAFE_METHODS
            and cookie_session
            and not explicit_credentials
            and not _origin_matches_request(scope, headers)
        ):
            response = JSONResponse(
                {"detail": "cross-site request blocked"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        incoming_request_id = str(headers.get("x-request-id") or "").strip()
        request_id = (
            incoming_request_id[:128]
            if incoming_request_id and incoming_request_id.isprintable()
            else uuid4().hex
        )
        path = str(scope.get("path") or "")

        async def send_hardened(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["X-Frame-Options"] = "DENY"
                response_headers["Referrer-Policy"] = "same-origin"
                response_headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=()"
                )
                response_headers["Cross-Origin-Opener-Policy"] = "same-origin"
                response_headers["Cross-Origin-Resource-Policy"] = "same-origin"
                if path.startswith(SENSITIVE_PREFIXES):
                    response_headers["Cache-Control"] = "no-store"
                if settings.app_env == "production":
                    response_headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_hardened)
