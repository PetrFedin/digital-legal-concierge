from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consultation_slots import SLOTS_HTML
from app.api.guided_lawyer_ui import (
    _CONSULTATION_DRAFT_PATCH,
    _WORKSPACE_DEEP_LINK_PATCH,
    _inject_patch,
)
from app.api.lawyer_consultation_desk import CONSULTATION_DESK_HTML
from app.api.lawyer_workspace import WORKSPACE_HTML
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["staff-ui-shell-guard"])


def _effective_token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_ui_actor(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    *,
    allowed_roles: frozenset[str],
):
    token = _effective_token(request, header_token)
    try:
        actor = await resolve_document_actor(db, token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return None
        # The canonical staff landing already explains unsupported roles and
        # other account prerequisites. Do not expose a raw JSON 403 from a UI
        # shell before the employee can even reach a recovery action.
        if error.status_code in {403, 409}:
            return "staff_landing"
        raise
    except HTTPException as error:
        # A valid lawyer session can still have a missing/inactive/unlinked
        # Lawyer business profile. Keep this fail-closed but human-readable.
        if error.status_code in {403, 409}:
            return "staff_landing"
        raise

    if actor.role not in allowed_roles:
        return "staff_landing"
    return actor


async def _require_lawyer_ui_actor(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    return await _require_ui_actor(
        request,
        db,
        header_token,
        allowed_roles=frozenset({ROLE_LAWYER}),
    )


async def _require_staff_ui_actor(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    return await _require_ui_actor(
        request,
        db,
        header_token,
        allowed_roles=frozenset({ROLE_LAWYER, ROLE_ADMIN, ROLE_SUPERADMIN}),
    )


def _gate_response(actor):
    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    if actor == "staff_landing":
        return RedirectResponse(url="/admin-ui", status_code=303)
    return None


async def _guarded_lawyer_html(
    *,
    request: Request,
    db: AsyncSession,
    x_admin_token: str | None,
    html: str,
    patch: str,
):
    actor = await _require_lawyer_ui_actor(request, db, x_admin_token)
    redirect = _gate_response(actor)
    if redirect is not None:
        return redirect
    return HTMLResponse(_inject_patch(html, patch))


@router.get("/lawyer/workspace/ui", response_class=HTMLResponse)
async def guarded_lawyer_workspace_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Authenticate the lawyer before returning the workspace HTML shell."""

    return await _guarded_lawyer_html(
        request=request,
        db=db,
        x_admin_token=x_admin_token,
        html=WORKSPACE_HTML,
        patch=_WORKSPACE_DEEP_LINK_PATCH,
    )


@router.get("/lawyer/consultation-desk/ui", response_class=HTMLResponse)
async def guarded_consultation_desk_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Authenticate the lawyer before returning consultation working UI."""

    return await _guarded_lawyer_html(
        request=request,
        db=db,
        x_admin_token=x_admin_token,
        html=CONSULTATION_DESK_HTML,
        patch=_CONSULTATION_DRAFT_PATCH,
    )


@router.get("/consultation-slots/ui", response_class=HTMLResponse)
async def guarded_consultation_slots_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Open the shared schedule only after a usable staff identity is resolved."""

    actor = await _require_staff_ui_actor(request, db, x_admin_token)
    redirect = _gate_response(actor)
    if redirect is not None:
        return redirect
    return HTMLResponse(SLOTS_HTML)


__all__ = ["router"]
