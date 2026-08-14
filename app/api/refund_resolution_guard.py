from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.refund_center import REFUND_CENTER_HTML
from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.refund_service import ConsultationRefundService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User
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
            "Возврат отклонён: устранить причину, повторно выполнить возврат и вернуть его в очередь"
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


@router.get("/admin/refunds/declined")
async def declined_refunds(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    rows = list(
        (
            await db.execute(
                select(Payment, Case, User)
                .join(Case, Case.id == Payment.case_id)
                .join(User, User.id == Case.client_id)
                .where(Payment.status == PaymentStatus.REFUND_DECLINED.value)
                .order_by(Payment.updated_at.asc(), Payment.id.asc())
                .limit(300)
            )
        ).all()
    )
    return [
        {
            "payment_id": int(payment.id),
            "case_id": int(case.id),
            "case_number": case.case_number,
            "case_status": str(case.status),
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
            "case_detail_url": f"/admin/workdesk/ui?case_id={case.id}",
            "active_booking_preserved": (
                str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value
            ),
            "retry_only": True,
        }
        for payment, case, user in rows
    ]


@router.post("/admin/refunds/{payment_id}/retry")
async def retry_declined_refund(
    payment_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    comment = str(payload.get("comment") or "").strip()
    if len(comment) < 10:
        raise HTTPException(
            status_code=400,
            detail="Опишите, что исправлено перед повторным возвратом — минимум 10 символов",
        )
    try:
        payment = (
            await db.execute(
                select(Payment)
                .where(Payment.id == int(payment_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if payment is None:
            raise LookupError("Платёж не найден")
        case = (
            await db.execute(
                select(Case)
                .where(Case.id == payment.case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело не найдено")
        if payment.status == PaymentStatus.REFUND_PENDING:
            await db.commit()
            return {
                "ok": True,
                "payment_id": int(payment.id),
                "case_id": int(case.id),
                "status": str(payment.status),
                "case_status": str(case.status),
            }
        if payment.status != PaymentStatus.REFUND_DECLINED:
            raise ValueError(
                "Повторно открыть можно только возврат со статусом REFUND_DECLINED"
            )

        old_status = str(payment.status)
        payment.status = PaymentStatus.REFUND_PENDING
        if str(case.status) == CaseStatus.M2_CONSULTATION_DONE.value:
            case.next_action = (
                "Повторный возврат открыт: выполнить фактическую операцию у провайдера и зафиксировать результат"
            )
        action = (
            "CONSULTATION_REFUND_REOPENED"
            if str(payment.payment_code) == PaymentCode.M2_CONSULTATION_PAYMENT.value
            else "M1_PAYMENT_REFUND_REOPENED"
        )
        await add_case_history_event(
            db,
            actor_type="admin",
            actor_id=int(actor.account_id),
            case_id=case.id,
            action=action,
            old_value={"payment_id": payment.id, "status": old_status},
            new_value={
                "payment_id": payment.id,
                "status": str(payment.status),
                "case_status_preserved": str(case.status),
            },
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
        "payment_id": int(payment.id),
        "case_id": int(case.id),
        "status": str(payment.status),
        "case_status": str(case.status),
    }


_REFUND_RETRY_UI_PATCH = r"""
<script>
(function(){
  const baseRenderRows=renderRows;
  function retryCard(x){
    return `<article style="background:#fff7e6;border:1px solid #fedf89;border-radius:14px;padding:14px;margin-bottom:12px"><div class="row"><div><span class="context other">Возврат требует повторной попытки</span><h3>${esc(x.case_number)} · платёж #${Number(x.payment_id)}</h3><div class="muted">${esc(x.client_name||'—')} · ${Number(x.amount).toLocaleString('ru-RU')} ${esc(x.currency||'RUB')}</div></div><b>${esc(x.status)}</b></div><div class="notice" style="margin-top:10px"><b>Деньги не отмечены возвращёнными.</b> Сначала устраните причину отказа у провайдера/банка. Затем верните платёж в рабочую очередь и после фактической операции отдельно нажмите «Возврат выполнен».</div><div class="actions"><button data-payment-id="${Number(x.payment_id)}" onclick="retryRefund(${Number(x.payment_id)},this)">Вернуть в очередь возврата</button><a class="button gray" href="/message-center/ui?case_id=${Number(x.case_id)}">Связаться с клиентом</a><a class="button gray" href="/admin/workdesk/ui?case_id=${Number(x.case_id)}">Открыть дело</a></div></article>`;
  }
  renderRows=function(rows){
    const retry=rows.filter(x=>x.retry_only),pending=rows.filter(x=>!x.retry_only);
    if(pending.length)baseRenderRows(pending);else content.innerHTML='';
    if(retry.length)content.insertAdjacentHTML('beforeend',retry.map(retryCard).join(''));
    if(!pending.length&&!retry.length)baseRenderRows([]);
  };
  window.retryRefund=async function(id,button){
    const comment=prompt('Что исправлено перед повторной попыткой возврата?');
    if(!comment)return;
    if(comment.trim().length<10){feedback('Комментарий должен содержать не менее 10 символов.','bad');return}
    if(!confirm(`Вернуть платёж #${id} в очередь возврата? Это не отправляет деньги и не меняет этап дела.`))return;
    return withPaymentAction(id,button,async()=>{
      try{
        const result=await api(`/admin/refunds/${id}/retry`,{method:'POST',body:JSON.stringify({comment:comment.trim()})});
        feedback(`Платёж #${result.payment_id} снова в очереди возврата. Выполните фактическую операцию у провайдера, затем зафиксируйте результат.`,'ok');
        await load();
      }catch(e){feedback('Возврат не переоткрыт: '+e.message,'bad')}
    });
  };
  load=async function(){
    const data=await Promise.all([api('/admin/refunds'),api('/admin/refunds/declined')]);
    const rows=[...(data[0]||[]),...(data[1]||[])],visible=visibleRows(rows);
    if(requestedPaymentId&&visible.length)terminalCaseId=Number(visible[0].case_id)||terminalCaseId;
    renderRows(visible);
  };
})();
</script>
"""


def _refund_ui_html() -> str:
    marker = "</body>"
    if REFUND_CENTER_HTML.count(marker) != 1:
        raise RuntimeError("Refund center template contract changed")
    return REFUND_CENTER_HTML.replace(marker, _REFUND_RETRY_UI_PATCH + marker, 1)


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
    return HTMLResponse(_refund_ui_html())
