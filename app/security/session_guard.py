from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.admin_user import AdminUser
from app.security.access_control import (
    ROLE_SUPERADMIN,
    decode_access_token,
    normalize_roles,
)


class AdminSessionGuardMiddleware(BaseHTTPMiddleware):
    """Reject stale, deactivated or non-MFA administrative sessions globally."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/login", "/mfa")):
            return await call_next(request)

        token = request.headers.get("x-admin-token") or request.cookies.get(
            settings.admin_session_cookie
        )
        if not token:
            return await call_next(request)

        payload = decode_access_token(token)
        if not payload:
            return JSONResponse(
                status_code=401,
                content={"detail": "Сессия недействительна или истекла"},
            )
        if payload.get("legacy"):
            return await call_next(request)

        try:
            user_id = int(payload.get("uid") or 0)
        except (TypeError, ValueError):
            user_id = 0
        if not user_id:
            return JSONResponse(status_code=401, content={"detail": "Некорректная сессия"})

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(AdminUser).where(AdminUser.id == user_id))
            ).scalar_one_or_none()
            if not user or not user.is_active:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Учётная запись отключена"},
                )
            current_roles = normalize_roles(user.role)
            token_roles = normalize_roles(payload.get("roles"))
            if set(current_roles) != set(token_roles):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Права изменены. Выполните вход повторно"},
                )
            if int(payload.get("sv") or 0) != int(user.session_version or 1):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Сессия отозвана"},
                )
            if ROLE_SUPERADMIN in current_roles and (
                not user.mfa_enabled or not bool(payload.get("mfa"))
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Для суперадминистратора обязательна MFA"},
                )

        return await call_next(request)
