from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.access_management import ACCESS_HTML
from app.api.audit_center import AUDIT_CENTER_HTML
from app.api.recovery_center import recovery_ui as legacy_recovery_ui
from app.api.retention_center import RETENTION_HTML
from app.api.security_event_center import SECURITY_EVENT_HTML
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["superadmin-ui-guards"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _superadmin_gate(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    """Resolve a current personal MFA superadmin or return guided UI recovery."""

    try:
        actor = await resolve_document_actor(db, _token(request, header_token))
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    if actor.role != ROLE_SUPERADMIN:
        return RedirectResponse(url="/admin-ui", status_code=303)
    return actor


async def _superadmin_html(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    html: str,
):
    gate = await _superadmin_gate(request, db, header_token)
    if isinstance(gate, RedirectResponse):
        return gate
    return HTMLResponse(html)


@router.get("/access/ui", response_class=HTMLResponse)
async def protected_access_management_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    return await _superadmin_html(request, db, x_admin_token, ACCESS_HTML)


@router.get("/audit-center/ui", response_class=HTMLResponse)
async def protected_audit_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Do not expose the audit console shell before superadmin auth resolves."""

    return await _superadmin_html(request, db, x_admin_token, AUDIT_CENTER_HTML)


@router.get("/security-events/ui", response_class=HTMLResponse)
async def protected_security_event_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Do not expose the security console shell before superadmin auth resolves."""

    return await _superadmin_html(request, db, x_admin_token, SECURITY_EVENT_HTML)


@router.get("/retention/ui", response_class=HTMLResponse)
async def protected_retention_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Protect legal-hold/deletion controls before their HTML is returned."""

    return await _superadmin_html(request, db, x_admin_token, RETENTION_HTML)


@router.get("/recovery-center/ui", response_class=HTMLResponse)
async def protected_recovery_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Protect high-impact recovery controls and keep their existing UI intact."""

    gate = await _superadmin_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    return await legacy_recovery_ui(
        request=request,
        db=db,
        x_admin_token=_token(request, x_admin_token),
    )


__all__ = ["router"]
