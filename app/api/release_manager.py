from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["release-manager"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/release-manager/status")
async def release_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    return {
        "ok": True,
        "status": "live_verification_required",
        "note": "Статическая версия/чек-лист не считается доказательством готовности релиза.",
        "workdesk": "/admin/workdesk/ui",
        "process_integrity": "/admin/workdesk/integrity",
        "health": "/health-center/ui",
        "diagnostics": "/diagnostic-center/ui",
        "backup": "/backup-center/ui",
        "security": "/security-events/ui",
        "audit": "/audit-center/ui",
    }


@router.get("/release-manager/ui")
async def release_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _admin(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return RedirectResponse(url="/diagnostic-center/ui", status_code=303)
