from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consultation_slots import SLOTS_HTML
from app.api.contract_center import CONTRACT_CENTER_HTML
from app.api.contract_workspace_ui import contract_aware_workspace_html
from app.api.document_access_portal import DOCUMENT_ACCESS_HTML
from app.api.guided_refund_center import REFUND_UI
from app.api.lawyer_consultation_decision_guard import guarded_consultation_desk_html
from app.api.payment_review_center import PAYMENT_REVIEW_CENTER_HTML
from app.api.sla_center import SLA_CENTER_HTML
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
        # The canonical staff landing explains unsupported roles and account
        # prerequisites. Keep the shell fail-closed without returning raw JSON.
        if error.status_code in {403, 409}:
            return "staff_landing"
        raise
    except Exception as error:
        # resolve_document_actor may surface FastAPI HTTPException from profile
        # prerequisites. Avoid importing/duplicating auth internals here.
        status_code = getattr(error, "status_code", None)
        if status_code in {403, 409}:
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


async def _require_admin_ui_actor(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    return await _require_ui_actor(
        request,
        db,
        header_token,
        allowed_roles=frozenset({ROLE_ADMIN, ROLE_SUPERADMIN}),
    )


def _gate_response(actor):
    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    if actor == "staff_landing":
        return RedirectResponse(url="/admin-ui", status_code=303)
    return None


async def _lawyer_gate(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await _require_lawyer_ui_actor(request, db, header_token)
    return _gate_response(actor)


async def _staff_shell(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    html: str,
):
    actor = await _require_staff_ui_actor(request, db, header_token)
    redirect = _gate_response(actor)
    if redirect is not None:
        return redirect
    return HTMLResponse(html)


async def _admin_shell(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    html: str,
):
    actor = await _require_admin_ui_actor(request, db, header_token)
    redirect = _gate_response(actor)
    if redirect is not None:
        return redirect
    return HTMLResponse(html)


@router.get("/lawyer/workspace/ui", response_class=HTMLResponse)
async def guarded_lawyer_workspace_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Authenticate first, then render the full composite lawyer workspace."""

    redirect = await _lawyer_gate(request, db, x_admin_token)
    if redirect is not None:
        return redirect
    return HTMLResponse(contract_aware_workspace_html())


@router.get("/lawyer/consultation-desk/ui", response_class=HTMLResponse)
async def guarded_consultation_desk_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Authenticate first, then keep draft + deterministic decision guards."""

    redirect = await _lawyer_gate(request, db, x_admin_token)
    if redirect is not None:
        return redirect
    return HTMLResponse(guarded_consultation_desk_html())


@router.get("/consultation-slots/ui", response_class=HTMLResponse)
async def guarded_consultation_slots_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    return await _staff_shell(request, db, x_admin_token, SLOTS_HTML)


@router.get("/document-access/ui", response_class=HTMLResponse)
async def guarded_document_access_portal_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    return await _staff_shell(request, db, x_admin_token, DOCUMENT_ACCESS_HTML)


@router.get("/contracts/ui", response_class=HTMLResponse)
async def guarded_contract_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    return await _staff_shell(request, db, x_admin_token, CONTRACT_CENTER_HTML)


@router.get("/admin/payment-reviews/ui", response_class=HTMLResponse)
async def guarded_payment_review_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    return await _admin_shell(
        request,
        db,
        x_admin_token,
        PAYMENT_REVIEW_CENTER_HTML,
    )


@router.get("/admin/refunds/ui", response_class=HTMLResponse)
async def guarded_refund_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    return await _admin_shell(request, db, x_admin_token, REFUND_UI)


@router.get("/admin/sla/ui", response_class=HTMLResponse)
async def guarded_sla_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    return await _admin_shell(request, db, x_admin_token, SLA_CENTER_HTML)


__all__ = ["router"]
