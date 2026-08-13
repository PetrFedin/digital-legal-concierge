from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["go-live-center"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_admin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/go-live/status")
async def go_live_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return {
        "ok": True,
        "status": "live_checks_required",
        "steps": [
            {"title": "Проверить E2E-целостность дел", "url": "/admin/workdesk/integrity"},
            {"title": "Проверить состояние сервиса", "url": "/health-center/ui"},
            {"title": "Проверить диагностику", "url": "/diagnostic-center/ui"},
            {"title": "Проверить события безопасности", "url": "/security-events/ui"},
            {"title": "Проверить целостность аудита", "url": "/audit-center/ui"},
        ],
        "canonical_ui": "/admin/workdesk/ui",
    }


@router.get("/go-live/ui")
async def go_live_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _require_admin(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return RedirectResponse(url="/admin/workdesk/ui", status_code=303)
