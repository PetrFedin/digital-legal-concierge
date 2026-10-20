"""One revocable, database-backed authority for current staff permissions."""
from __future__ import annotations

from collections.abc import Collection

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.admin_user import AdminUser
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_SUPERADMIN,
    decode_access_token,
    normalize_roles,
)
from app.security.token_revocation import is_token_revoked


async def require_current_staff_token(
    db: AsyncSession,
    token: str | None,
    *,
    allowed_roles: Collection[str],
    allow_test_legacy: bool = False,
) -> dict:
    """Check token authenticity *and* the live user/session state on each read/write."""
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Требуется вход сотрудника")
    allowed = set(allowed_roles)
    token_roles = set(normalize_roles(payload.get("roles")))
    if not token_roles.intersection(allowed):
        raise HTTPException(status_code=403, detail="Недостаточно полномочий")

    if payload.get("legacy"):
        # Compatibility for isolated test fixtures only; never real accounts.
        if allow_test_legacy and settings.app_env in {"local", "test"}:
            return payload
        raise HTTPException(status_code=403, detail="Требуется персональная сессия")

    if not str(payload.get("jti") or ""):
        raise HTTPException(status_code=403, detail="Недействительная сессия")
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
    live_roles = set(normalize_roles(account.role)) if account else set()
    if (
        account is None
        or not account.is_active
        or live_roles != token_roles
        or not live_roles.intersection(allowed)
        or int(account.session_version or 1) != session_version
    ):
        raise HTTPException(status_code=403, detail="Права сотрудника изменились")
    if ROLE_SUPERADMIN in token_roles and (
        payload.get("mfa") is not True or not account.mfa_enabled
    ):
        raise HTTPException(status_code=403, detail="Требуется действующее MFA")
    return payload


async def require_current_settings_admin(
    request: Request,
    db: AsyncSession,
    header_token: str | None = None,
) -> dict:
    """Settings never accept the shared legacy token, including in tests."""
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    if not token:
        raise HTTPException(status_code=403, detail="Требуется персональный вход администратора")
    return await require_current_staff_token(
        db, token, allowed_roles={ROLE_ADMIN}, allow_test_legacy=False,
    )
