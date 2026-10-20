"""Live revocable authorization for payment and SLA configuration."""
from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.admin_user import AdminUser
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_SUPERADMIN,
    decode_access_token,
    has_role,
    normalize_roles,
)
from app.security.token_revocation import is_token_revoked


async def require_current_settings_admin(
    request: Request,
    db: AsyncSession,
    header_token: str | None = None,
) -> dict:
    """Require an active, unrevoked, personal administrator session.

    A correctly signed token is insufficient after logout, role demotion,
    account deactivation or session-version rotation.
    """
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    payload = decode_access_token(token)
    if (
        not payload
        or payload.get("legacy")
        or not str(payload.get("jti") or "")
        or not has_role(payload.get("roles"), ROLE_ADMIN)
    ):
        raise HTTPException(status_code=403, detail="Требуется персональный вход администратора")
    try:
        uid = int(payload.get("uid") or 0)
        session_version = int(payload.get("sv") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="Недействительная сессия") from None
    if uid <= 0 or session_version <= 0:
        raise HTTPException(status_code=403, detail="Недействительная сессия")
    if await is_token_revoked(db, token):
        raise HTTPException(status_code=401, detail="Сессия отозвана. Войдите повторно")

    account = await db.get(AdminUser, uid)
    token_roles = set(normalize_roles(payload.get("roles")))
    live_roles = set(normalize_roles(account.role)) if account else set()
    if (
        account is None
        or not account.is_active
        or ROLE_ADMIN not in live_roles
        or live_roles != token_roles
        or int(account.session_version or 1) != session_version
    ):
        raise HTTPException(status_code=403, detail="Права администратора изменились")
    if ROLE_SUPERADMIN in token_roles and (
        payload.get("mfa") is not True or not account.mfa_enabled
    ):
        raise HTTPException(status_code=403, detail="Требуется действующее MFA")
    return payload
