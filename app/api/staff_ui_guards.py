from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.document_review import REVIEW_HTML
from app.api.health_center import health_center_ui as legacy_health_center_ui
from app.api.payment_review_center import PAYMENT_REVIEW_CENTER_HTML
from app.api.sla_center import SLA_CENTER_HTML
from app.config import settings
from app.db.session import get_db
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.payment import Payment
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["staff-ui-guards"])
_M2_OPEN_LINK_STATUSES = {
    PaymentStatus.PENDING.value,
    PaymentStatus.WAITING_CONFIRMATION.value,
}


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


async def _staff_gate(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    *,
    staff: bool = False,
):
    """Resolve a UI actor without exposing raw JSON authorization dead ends."""

    try:
        actor = (
            await _staff(request, db, header_token)
            if staff
            else await _admin(request, db, header_token)
        )
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
    return actor


async def _guarded_html(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    html: str,
    *,
    staff: bool = False,
):
    gate = await _staff_gate(
        request,
        db,
        header_token,
        staff=staff,
    )
    if isinstance(gate, RedirectResponse):
        return gate
    return HTMLResponse(html)


@router.get("/admin/payment-reviews/ui", response_class=HTMLResponse)
async def protected_payment_review_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    gate = await _staff_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate

    # Process Integrity can point to a PENDING M2 provider link that is stale but
    # has not received money. Such a record is intentionally absent from the
    # PAID_REVIEW queue. Route that exact payment to its non-financial
    # reservation-reconciliation screen instead of opening an empty review page.
    raw_payment_id = str(request.query_params.get("payment_id") or "").strip()
    try:
        payment_id = int(raw_payment_id) if raw_payment_id else None
    except ValueError:
        payment_id = None
    if payment_id and payment_id > 0:
        payment = await db.get(Payment, payment_id)
        if (
            payment is not None
            and str(payment.payment_code) == PaymentCode.M2_CONSULTATION_PAYMENT.value
            and str(payment.status) in _M2_OPEN_LINK_STATUSES
        ):
            return RedirectResponse(
                url=(
                    f"/admin/workdesk/payments/{int(payment.id)}/reconcile-m2-reservation/ui"
                    f"?case_id={int(payment.case_id)}"
                ),
                status_code=303,
            )

    return HTMLResponse(PAYMENT_REVIEW_CENTER_HTML)


@router.get("/admin/technical-cases/ui")
async def retired_technical_cases_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Retire the old technical-case destination into the canonical Workdesk.

    Process Integrity and historical bookmarks may still point here. Do not
    create a second troubleshooting cabinet: authenticate the admin, preserve a
    valid case deep-link and redirect to the one operational control surface.
    """

    gate = await _staff_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate

    raw_case_id = str(request.query_params.get("case_id") or "").strip()
    try:
        case_id = int(raw_case_id) if raw_case_id else None
    except ValueError:
        case_id = None
    target = (
        f"/admin/workdesk/ui?case_id={case_id}"
        if case_id is not None and case_id > 0
        else "/admin/workdesk/ui"
    )
    return RedirectResponse(url=target, status_code=303)


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


@router.get("/health-center/ui", response_class=HTMLResponse)
async def protected_health_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Keep operational health checks on the admin recovery path, not raw 403."""

    gate = await _staff_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    return await legacy_health_center_ui(
        request=request,
        db=db,
        x_admin_token=x_admin_token,
    )
