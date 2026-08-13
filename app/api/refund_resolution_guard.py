from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.refund_center import REFUND_CENTER_HTML
from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_service import CaseService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.refund_service import ConsultationRefundService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["refund-resolution-guard"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


async def _lawyer_no_show_refund_recorded(db: AsyncSession, case_id: int) -> bool:
    event_id = (
        await db.execute(
            select(AuditLog.id)
            .where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id == int(case_id),
                AuditLog.action == "CONSULTATION_LAWYER_NO_SHOW_REFUND_REQUESTED",
            )
            .order_by(AuditLog.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return event_id is not None


async def _finish_m2_refund_case(
    db: AsyncSession,
    *,
    case: Case,
    actor_id: int,
    decision: str,
    comment: str,
) -> None:
    status = (
        case.status
        if isinstance(case.status, CaseStatus)
        else CaseStatus(str(case.status))
    )

    if decision == "refunded":
        # New lawyer-no-show refunds enter DONE when the refund is requested.
        # Repair historical rows that still say BOOKED only when the audit trail
        # proves this exact lawyer-no-show refund path.
        if status == CaseStatus.M2_CONSULTATION_BOOKED and await _lawyer_no_show_refund_recorded(
            db, case.id
        ):
            await CaseService(db).change_status(
                case=case,
                next_status=CaseStatus.M2_CONSULTATION_DONE,
                actor_type="admin",
                actor_id=actor_id,
                comment="Восстановлена стадия M2 после подтверждённого возврата за неявку юриста",
            )
            status = CaseStatus.M2_CONSULTATION_DONE
        if status == CaseStatus.M2_CONSULTATION_DONE:
            await CaseService(db).change_status(
                case=case,
                next_status=CaseStatus.M2_CLOSED,
                actor_type="admin",
                actor_id=actor_id,
                comment=(
                    "Фактический возврат после отменённой консультации подтверждён; "
                    "финансовый и консультационный контуры завершены"
                ),
            )
    elif status == CaseStatus.M2_CONSULTATION_DONE:
        case.next_action = (
            "Возврат отклонён: проверить причину, связаться с клиентом и принять новое решение"
        )


@router.post("/admin/refunds/{payment_id}/resolve")
async def resolve_refund_guard(
    payment_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    decision = str(payload.get("decision") or "").strip().lower()
    comment = str(payload.get("comment") or "").strip()
    try:
        payment = await ConsultationRefundService(db).resolve_refund(
            payment_id=payment_id,
            decision=decision,
            actor_id=int(actor.account_id),
            comment=comment,
        )
        case = (
            await db.execute(
                select(Case)
                .where(Case.id == payment.case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело не найдено")
        if str(payment.payment_code) == PaymentCode.M2_CONSULTATION_PAYMENT.value:
            await _finish_m2_refund_case(
                db,
                case=case,
                actor_id=int(actor.account_id),
                decision=decision,
                comment=comment,
            )
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
    return {
        "ok": True,
        "payment_id": payment.id,
        "case_id": payment.case_id,
        "status": payment.status,
        "case_status": str(case.status),
    }


@router.get("/admin/refunds/ui", response_class=HTMLResponse)
async def refund_ui_guard(
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
    return HTMLResponse(REFUND_CENTER_HTML)
