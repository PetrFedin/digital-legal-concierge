from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.document_review import REVIEW_HTML
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
    try:
        await _admin(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise

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
