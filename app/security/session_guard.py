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
from app.security.token_revocation import is_token_revoked


class AdminSessionGuardMiddleware(BaseHTTPMiddleware):
    """Reject stale, revoked, deactivated or non-MFA admin sessions globally."""

    @staticmethod
    def _rejected(status_code: int, detail: str) -> JSONResponse:
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        response.delete_cookie(settings.admin_session_cookie, path="/")
        return response

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/login", "/mfa", "/logout")):
            return await call_next(request)

        token = request.headers.get("x-admin-token") or request.cookies.get(
            settings.admin_session_cookie
        )
        if not token:
            return await call_next(request)

        payload = decode_access_token(token)
        if not payload:
            return self._rejected(401, "Сессия недействительна или истекла")
        if payload.get("legacy"):
            return await call_next(request)

        try:
            user_id = int(payload.get("uid") or 0)
        except (TypeError, ValueError):
            user_id = 0
        if not user_id:
            return self._rejected(401, "Некорректная сессия")

        async with AsyncSessionLocal() as db:
            if await is_token_revoked(db, token):
                return self._rejected(401, "Сессия завершена")
            user = (
                await db.execute(select(AdminUser).where(AdminUser.id == user_id))
            ).scalar_one_or_none()
            if not user or not user.is_active:
                return self._rejected(401, "Учётная запись отключена")
            current_roles = normalize_roles(user.role)
            token_roles = normalize_roles(payload.get("roles"))
            if set(current_roles) != set(token_roles):
                return self._rejected(
                    401,
                    "Права изменены. Выполните вход повторно",
                )
            if int(payload.get("sv") or 0) != int(user.session_version or 1):
                return self._rejected(401, "Сессия отозвана")
            if ROLE_SUPERADMIN in current_roles and (
                not user.mfa_enabled or not bool(payload.get("mfa"))
            ):
                return self._rejected(
                    403,
                    "Для суперадминистратора обязательна MFA",
                )

        return await call_next(request)
