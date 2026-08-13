from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.legacy_center_guards import router as legacy_center_guards_router
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

# Keep this router unprefixed so compatibility guards can own the exact legacy
# URLs before the older center routers are mounted in app.main.
router = APIRouter(tags=["maintenance-center"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_admin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/maintenance-center/status")
async def maintenance_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return {
        "ok": True,
        "workdesk": "/admin/workdesk/ui",
        "process_integrity": "/admin/workdesk/integrity",
        "health": "/health-center/ui",
        "diagnostics": "/diagnostic-center/ui",
        "notifications": "/admin/notification-delivery/ui",
        "audit": "/audit-center/ui",
        "security": "/security-events/ui",
        "recovery": "/recovery-center/ui",
    }


@router.get("/maintenance-center/ui")
async def maintenance_ui(
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


# These routes are deliberately registered after the maintained maintenance
# endpoints but before final_qa_center/retention_center are included by main.py.
router.include_router(legacy_center_guards_router)
