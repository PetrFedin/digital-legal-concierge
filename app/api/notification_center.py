from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.notification_delivery import router as notification_delivery_router
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["notification-center"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_admin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/notification-center/status")
async def notification_center_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return {
        "ok": True,
        "canonical_delivery_queue": "/admin/notification-delivery",
        "canonical_ui": "/admin/notification-delivery/ui",
        "workdesk": "/admin/workdesk/ui",
        "message_center": "/message-center/ui",
    }


@router.get("/notification-center/ui")
async def notification_center_ui(
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
    return RedirectResponse(url="/admin/notification-delivery/ui", status_code=303)


# Keep the maintained delivery router registered through the existing
# notification-center entry in main.py.
router.include_router(notification_delivery_router)
