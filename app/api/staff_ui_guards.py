from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.document_review import REVIEW_HTML
from app.api.payment_review_center import PAYMENT_REVIEW_CENTER_HTML
from app.api.sla_center import SLA_CENTER_HTML
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["staff-ui-guards"])


async def _actor(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    return await resolve_document_actor(db, token)


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await _actor(request, db, header_token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


async def _staff(request: Request, db: AsyncSession, header_token: str | None):
    actor = await _actor(request, db, header_token)
    if actor.role not in {ROLE_LAWYER, ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для юридической команды")
    return actor


async def _guarded_html(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    html: str,
    *,
    staff: bool = False,
):
    try:
        if staff:
            await _staff(request, db, header_token)
        else:
            await _admin(request, db, header_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return HTMLResponse(html)


@router.get("/admin/payment-reviews/ui", response_class=HTMLResponse)
async def protected_payment_review_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    return await _guarded_html(
        request,
        db,
        x_admin_token,
        PAYMENT_REVIEW_CENTER_HTML,
    )


@router.get("/admin/sla/ui", response_class=HTMLResponse)
async def protected_sla_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    return await _guarded_html(request, db, x_admin_token, SLA_CENTER_HTML)


@router.get("/document-access/review/ui", response_class=HTMLResponse)
async def protected_document_review_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Serve the document decision cabinet only after personal staff auth.

    The underlying /document-access/review APIs were already role-aware, but the
    historical HTML shell itself was public and only discovered authorization
    after JavaScript started loading. This early route is mounted before the
    legacy document-access composite router, so anonymous users are redirected
    to login without receiving the staff interface.
    """

    return await _guarded_html(
        request,
        db,
        x_admin_token,
        REVIEW_HTML,
        staff=True,
    )
