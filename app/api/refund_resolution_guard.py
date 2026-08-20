"""Route-free facade for refund resolution helpers.

Runtime ownership of the existing ``/admin/refunds/*`` paths belongs to
``app.api.refund_product``. Historical presentation/resolution helpers remain in
``refund_resolution_guard_impl``; the retry mutation delegates to the canonical
refund domain service so concurrency rules are shared with final resolution.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import refund_resolution_guard_impl as _impl
from app.db.session import get_db
from app.domain.payments.refund_service import ConsultationRefundService

router = APIRouter(tags=["refund-resolution-retired"])

resolve_refund_guard = _impl.resolve_refund_guard
declined_refunds = _impl.declined_refunds
refund_ui_guard = _impl.refund_ui_guard
_refund_ui_html = _impl._refund_ui_html
_finish_m2_refund_case = _impl._finish_m2_refund_case
_lawyer_no_show_refund_recorded = _impl._lawyer_no_show_refund_recorded


async def retry_declined_refund(
    payment_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _impl._admin(request, db, x_admin_token)
    comment = str(payload.get("comment") or "").strip()
    if len(comment) < 10:
        raise HTTPException(
            status_code=400,
            detail="Опишите, что исправлено перед повторным возвратом — минимум 10 символов",
        )
    try:
        payment, case = await ConsultationRefundService(db).reopen_declined_refund(
            payment_id=payment_id,
            actor_id=int(actor.account_id),
            comment=comment,
        )
        response = {
            "ok": True,
            "payment_id": int(payment.id),
            "case_id": int(case.id),
            "status": str(payment.status),
            "case_status": str(case.status),
        }
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return response


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = [
    "declined_refunds",
    "refund_ui_guard",
    "resolve_refund_guard",
    "retry_declined_refund",
    "router",
]
