from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.payments.mode import payment_mode_valid
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import resolve_document_actor
from app.security.keyring import security_key_status

router = APIRouter(tags=["security"])


def build_security_checks() -> dict:
    """Return non-secret live checks; never enumerate which credential is set."""

    key_state = security_key_status()
    checks = {
        "security_keyring": bool(key_state.get("ok")),
        "payment_mode": bool(payment_mode_valid()),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "security_events": "/security-events/ui",
        "audit": "/audit-center/ui",
        "diagnostics": "/diagnostic-center/ui",
    }


@router.get("/security-check")
async def security_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return build_security_checks()
