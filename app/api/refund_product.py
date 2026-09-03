"""Single runtime owner for the existing M1/M2 refund staff surface.

This consolidates the historical ``refund_center``, ``guided_refund_center`` and
``refund_resolution_guard`` route layers without adding a new business flow.
Money is still moved outside the application at the configured provider; these
endpoints only expose the existing queue and record the verified administrative
result through the canonical refund domain service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.refund_resolution_guard import (
    declined_refunds,
    refund_ui_guard,
    resolve_refund_guard,
    retry_declined_refund,
)
from app.config import settings
from app.db.session import get_db
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import resolve_document_actor

router = APIRouter(prefix="/admin/refunds", tags=["admin", "refunds"])


async def _require_admin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


def _kind(payment: Payment) -> tuple[str, str]:
    if str(payment.payment_code) == PaymentCode.M2_CONSULTATION_PAYMENT.value:
        return "M2", "Оплата консультации"
    return "M1", payment.title or "Платёж M1"


async def _pending_rows(db: AsyncSession):
    return list(
        (
            await db.execute(
                select(Payment, Case, User)
                .join(Case, Case.id == Payment.case_id)
                .join(User, User.id == Case.client_id)
                .where(Payment.status == PaymentStatus.REFUND_PENDING.value)
                .order_by(Payment.updated_at.asc(), Payment.id.asc())
            )
        ).all()
    )


@router.get("")
async def list_refund_requests(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    rows = await _pending_rows(db)
    return [
        {
            "payment_id": int(payment.id),
            "case_id": int(case.id),
            "case_number": case.case_number,
            "case_status": str(case.status),
            "active_booking_preserved": (
                str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value
            ),
            "case_detail_url": f"/admin/workdesk/ui?case_id={int(case.id)}",
            "client_id": int(user.id),
            "client_name": user.full_name,
            "telegram_id": user.telegram_id,
            "amount": float(payment.amount),
            "currency": payment.currency,
            "provider": payment.provider,
            "provider_payment_id": payment.provider_payment_id,
            "status": str(payment.status),
            "requested_at": (
                payment.updated_at.isoformat() if payment.updated_at else None
            ),
        }
        for payment, case, user in rows
    ]


@router.get("/context")
async def refund_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Compatibility projection for the already-shipped guided refund UI."""

    await _require_admin(request, db, x_admin_token)
    rows = await _pending_rows(db)
    result = []
    for payment, case, user in rows:
        route, kind = _kind(payment)
        result.append(
            {
                "payment_id": int(payment.id),
                "payment_code": str(payment.payment_code),
                "payment_title": payment.title,
                "kind": kind,
                "route": route,
                "case_id": int(case.id),
                "case_number": case.case_number,
                "case_status": str(case.status),
                "case_route": case.route,
                "case_preserved": True,
                "active_booking_preserved": bool(
                    route == "M2"
                    and str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value
                ),
                "client_name": user.full_name,
                "telegram_id": user.telegram_id,
                "amount": float(payment.amount),
                "currency": payment.currency,
                "provider": payment.provider,
                "provider_payment_id": payment.provider_payment_id,
                "requested_at": (
                    payment.updated_at.isoformat() if payment.updated_at else None
                ),
                "case_detail_url": f"/admin/workdesk/ui?case_id={int(case.id)}",
            }
        )
    return result


# Reuse the hardened mutation/UI implementations without mounting their legacy
# router. The product router is the sole FastAPI owner of these public paths.
router.add_api_route(
    "/{payment_id}/resolve",
    resolve_refund_guard,
    methods=["POST"],
    name="resolve_refund",
)
router.add_api_route(
    "/declined",
    declined_refunds,
    methods=["GET"],
    name="declined_refunds",
)
router.add_api_route(
    "/{payment_id}/retry",
    retry_declined_refund,
    methods=["POST"],
    name="retry_declined_refund",
)
router.add_api_route(
    "/ui",
    refund_ui_guard,
    methods=["GET"],
    name="refund_center_ui",
)

__all__ = ["router"]
