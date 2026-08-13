from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["operations-center"])


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


@router.get("/operations-center/ui")
async def operations_center_ui(
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
    # The guided workdesk is now the canonical operational UI. Keep the legacy
    # URL as a safe compatibility redirect instead of maintaining a demo-like
    # second set of queues with different semantics.
    return RedirectResponse(url="/admin/workdesk/ui", status_code=303)


@router.get("/operations-center/status")
async def operations_center_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return {
        "ok": True,
        "canonical_ui": "/admin/workdesk/ui",
        "queues": {
            "unassigned": "/admin/work-queues/unassigned",
            "documents": "/admin/work-queues/documents",
            "consultations": "/admin/work-queues/consultations",
            "overdue": "/admin/work-queues/overdue",
        },
        "process_integrity": "/admin/workdesk/integrity",
        "messages": "/message-center/ui",
    }
