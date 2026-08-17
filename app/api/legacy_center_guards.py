from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.retention_center import RETENTION_HTML
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["legacy-center-guards"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _actor(request: Request, db: AsyncSession, header_token: str | None):
    return await resolve_document_actor(db, _token(request, header_token))


def _role_recovery() -> RedirectResponse:
    return RedirectResponse(url="/admin-ui", status_code=303)


@router.get("/final-qa/status")
async def final_qa_status_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _actor(request, db, x_admin_token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return {
        "ok": True,
        "status": "live_verification_required",
        "workdesk": "/admin/workdesk/ui",
        "process_integrity": "/admin/workdesk/integrity",
        "health": "/health-center/ui",
        "security": "/security-events/ui",
        "audit": "/audit-center/ui",
    }


@router.get("/final-qa/ui")
async def final_qa_ui_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _actor(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return _role_recovery()
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return _role_recovery()
        raise
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        return _role_recovery()
    return RedirectResponse(url="/admin/workdesk/ui", status_code=303)


@router.get("/retention/ui", response_class=HTMLResponse)
async def retention_ui_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _actor(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return _role_recovery()
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return _role_recovery()
        raise
    if actor.role != ROLE_SUPERADMIN:
        return _role_recovery()
    return HTMLResponse(RETENTION_HTML)
