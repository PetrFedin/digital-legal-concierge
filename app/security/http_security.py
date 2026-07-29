from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
WEBHOOK_PREFIXES = ("/webhooks/",)
SENSITIVE_PREFIXES = (
    "/admin",
    "/access",
    "/auth",
    "/login",
    "/logout",
    "/lawyer",
    "/mfa",
    "/operator",
    "/backup",
    "/maintenance",
    "/production",
    "/security",
)


def normalize_origin(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(str(value).strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    port = parsed.port
    if port and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def request_origin(request: Request) -> str | None:
    return normalize_origin(str(request.base_url))


def allowed_origins(request: Request) -> set[str]:
    values = {
        request_origin(request),
        normalize_origin(settings.public_base_url),
    }
    return {value for value in values if value}


class RequestOriginGuardMiddleware(BaseHTTPMiddleware):
    """Protect ambient cookie sessions from cross-site state-changing requests."""

    @staticmethod
    def _forbidden(detail: str) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": detail})

    async def dispatch(self, request: Request, call_next):
        if request.method.upper() in SAFE_METHODS:
            return await call_next(request)
        if request.url.path.startswith(WEBHOOK_PREFIXES):
            return await call_next(request)
        if request.headers.get("x-admin-token"):
            return await call_next(request)

        session_cookie = request.cookies.get(settings.admin_session_cookie)
        challenge_cookie = request.cookies.get("dlc_mfa_challenge")
        has_ambient_credentials = bool(session_cookie or challenge_cookie)
        fetch_site = str(request.headers.get("sec-fetch-site") or "").lower()
        if fetch_site == "cross-site" and has_ambient_credentials:
            return self._forbidden("Cross-site запрос отклонён")

        supplied_origin = normalize_origin(request.headers.get("origin"))
        if not supplied_origin:
            supplied_origin = normalize_origin(request.headers.get("referer"))
        if supplied_origin and supplied_origin not in allowed_origins(request):
            return self._forbidden("Источник запроса не разрешён")
        if (
            has_ambient_credentials
            and settings.app_env == "production"
            and not supplied_origin
        ):
            return self._forbidden(
                "Для изменения данных требуется подтверждённый Origin"
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply browser hardening headers without exposing configuration secrets."""

    CSP = "; ".join(
        [
            "default-src 'self'",
            "base-uri 'none'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "img-src 'self' data:",
            "style-src 'self' 'unsafe-inline'",
            "script-src 'self' 'unsafe-inline'",
            "connect-src 'self'",
            "font-src 'self' data:",
        ]
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy", self.CSP)
        if request.url.path.startswith(SENSITIVE_PREFIXES):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        if settings.app_env == "production":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response
