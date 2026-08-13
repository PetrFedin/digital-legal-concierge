from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["monitoring-center"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_admin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/monitoring-center/ui")
async def monitoring_center_ui(
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
    # The old v25 page duplicated several operational screens and exposed a
    # false sense of a separate monitoring product. Keep the URL compatible but
    # route staff to the maintained, authenticated diagnostic contour.
    return RedirectResponse(url="/diagnostic-center/ui", status_code=303)


@router.get("/monitoring-center/status")
async def monitoring_center_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    storage = Path(settings.storage_dir)
    return {
        "ok": True,
        "version": "1.0.0-v46",
        "storage_ready": storage.exists(),
        "run_bot": bool(settings.run_bot),
        "run_scheduler": bool(settings.run_scheduler),
        "payment_provider": settings.payment_provider,
        "canonical_ui": "/diagnostic-center/ui",
        "workdesk": "/admin/workdesk/ui",
    }
