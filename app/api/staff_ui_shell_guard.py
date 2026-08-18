from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
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
    """Authenticate first, then render the full composite lawyer workspace.

    The protected shell must preserve every business patch already present on
    the effective lawyer UI: M2 slot responsibility/deep-linking, M1 rejection,
    POA receipt, court evidence and the contract center primary action. Serving
    only the base WORKSPACE_HTML here would silently remove those actions because
    this early route intentionally wins FastAPI route precedence.
    """

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
    """Open the shared schedule only after a usable staff identity is resolved."""

    return await _staff_shell(request, db, x_admin_token, SLOTS_HTML)


@router.get("/document-access/ui", response_class=HTMLResponse)
async def guarded_document_access_portal_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Authenticate staff before returning the protected-materials HTML shell.

    The document APIs already enforce per-case M1/M2 responsibility and one-time
    download grants. The historical portal shell, however, was returned before
    any server-side staff check and only discovered session/role problems in
    JavaScript. This exact early route closes that UI boundary without weakening
    the existing document authorization rules or changing case_id deep links.
    """

    return await _staff_shell(request, db, x_admin_token, DOCUMENT_ACCESS_HTML)


@router.get("/contracts/ui", response_class=HTMLResponse)
async def guarded_contract_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Give old/direct contract bookmarks the same guided staff recovery.

    Contract APIs still enforce exact case responsibility and M1 stage rules.
    This shell guard only prevents an expired/wrong-role staff session from being
    misreported as a generic fresh-login problem and keeps the visible contract
    center on the canonical staff recovery path.
    """

    return await _staff_shell(request, db, x_admin_token, CONTRACT_CENTER_HTML)


@router.get("/admin/payment-reviews/ui", response_class=HTMLResponse)
async def guarded_payment_review_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Do not expose the sensitive payment-review shell before admin auth."""

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
    """Keep refund operations on the guided UI behind a personal admin session."""

    return await _admin_shell(request, db, x_admin_token, REFUND_UI)


@router.get("/admin/sla/ui", response_class=HTMLResponse)
async def guarded_sla_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Require admin identity before returning SLA/action-plan client data."""

    return await _admin_shell(request, db, x_admin_token, SLA_CENTER_HTML)


__all__ = ["router"]