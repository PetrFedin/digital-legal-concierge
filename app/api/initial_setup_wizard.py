from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.operator_guard import router as operator_guard_router
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["initial-setup"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_admin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/initial-setup-wizard/status")
async def setup_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return {
        "ok": True,
        "status": "managed_in_admin_settings",
        "settings": "/settings-ui",
        "workdesk": "/admin/workdesk/ui",
        "diagnostics": "/diagnostic-center/ui",
    }


@router.get("/initial-setup-wizard/ui")
async def setup_ui(
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
    return RedirectResponse(url="/settings-ui", status_code=303)


# This router is mounted before the legacy operator router in app.main, so the
# old anonymous shell cannot be served even though its source remains for URL
# compatibility during the v37 migration.
router.include_router(operator_guard_router)
