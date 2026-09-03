from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["task-center"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/task-center/status")
async def task_center_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    return {
        "ok": True,
        "status": "consolidated_into_workdesk",
        "workdesk": "/admin/workdesk/ui",
        "attention": "/admin/workdesk/attention",
        "process_integrity": "/admin/workdesk/integrity",
        "refunds": "/admin/refunds/ui",
        "payment_reviews": "/admin/payment-reviews/ui",
        "messages": "/message-center/ui",
        "notifications": "/admin/notification-delivery/ui",
    }


@router.get("/task-center/ui")
async def task_center_ui(
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
    # The historical task page used a different priority order and counted
    # non-actionable M2 cases as "without lawyer". Workdesk is now the single
    # source of truth for payment/refund/SLA/message/assignment/document queues.
    return RedirectResponse(url="/admin/workdesk/ui", status_code=303)
