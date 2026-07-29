from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.payments.payment_review_service import (
    PaymentReviewResolutionError,
    PaymentReviewService,
)
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, decode_access_token, has_role

router = APIRouter(
    prefix="/admin/payment-reviews",
    tags=["admin", "payment-reviews"],
)


def require_admin(token: str | None) -> dict:
    payload = decode_access_token(token)
    if not payload or not has_role(payload.get("roles"), ROLE_ADMIN):
        raise HTTPException(
            status_code=403,
            detail="Доступ только для администратора",
        )
    return payload


def actor_id_from_token(payload: dict) -> int | None:
    try:
        value = int(payload.get("uid") or 0)
    except (TypeError, ValueError):
        value = 0
    return value or None


async def latest_consultation(
    db: AsyncSession,
    case_id: int,
) -> Consultation | None:
    return (
        await db.execute(
            select(Consultation)
            .where(Consultation.case_id == case_id)
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            .limit(1)
        )
    ).scalars().first()


async def latest_review_reason(
    db: AsyncSession,
    case_id: int,
) -> str | None:
    event = (
        await db.execute(
            select(AuditLog)
            .where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
                AuditLog.action == "CONSULTATION_PAYMENT_REVIEW_REQUIRED",
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if not event or not event.new_value:
        return None
    return event.new_value.get("reason")


@router.get("")
async def list_payment_reviews(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    payments = (
        await db.execute(
            select(Payment)
            .where(Payment.status == PaymentStatus.PAID_REVIEW)
            .order_by(Payment.updated_at.asc(), Payment.id.asc())
        )
    ).scalars().all()

    result = []
    for payment in payments:
        case = await db.get(Case, payment.case_id)
        user = await db.get(User, case.client_id) if case else None
        consultation = (
            await latest_consultation(db, case.id) if case else None
        )
        existing_booking_valid = False
        if consultation and consultation.slot_id:
            slot = await SlotService(db).get_slot(consultation.slot_id)
            existing_booking_valid = bool(
                slot
                and slot.status == "booked"
                and slot.consultation_id == consultation.id
            )

        result.append(
            {
                "payment_id": payment.id,
                "case_id": case.id if case else payment.case_id,
                "case_number": case.case_number if case else None,
                "client_name": user.full_name if user else None,
                "telegram_id": user.telegram_id if user else None,
                "amount": float(payment.amount),
                "currency": payment.currency,
                "provider": payment.provider,
                "provider_payment_id": payment.provider_payment_id,
                "reservation_key": payment.reservation_key,
                "status": payment.status,
                "reason": (
                    await latest_review_reason(db, payment.case_id)
                ),
                "consultation_id": (
                    consultation.id if consultation else None
                ),
                "consultation_status": (
                    consultation.status if consultation else None
                ),
                "current_slot_id": (
                    consultation.slot_id if consultation else None
                ),
                "scheduled_at": (
                    consultation.scheduled_at.isoformat()
                    if consultation and consultation.scheduled_at
                    else None
                ),
                "existing_booking_valid": existing_booking_valid,
                "created_at": (
                    payment.created_at.isoformat()
                    if payment.created_at
                    else None
                ),
            }
        )
    return result


@router.get("/slots")
async def list_available_review_slots(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    slots = await SlotService(db).get_available_slots(limit=200)
    lawyer_ids = {slot.lawyer_id for slot in slots}
    lawyers = {
        lawyer.id: lawyer
        for lawyer in (
            await db.execute(
                select(Lawyer).where(Lawyer.id.in_(lawyer_ids))
            )
        ).scalars().all()
    } if lawyer_ids else {}
    return [
        {
            "id": slot.id,
            "lawyer_id": slot.lawyer_id,
            "lawyer_name": (
                lawyers[slot.lawyer_id].full_name
                if slot.lawyer_id in lawyers
                else f"Юрист #{slot.lawyer_id}"
            ),
            "starts_at": slot.starts_at.isoformat(),
            "ends_at": slot.ends_at.isoformat(),
        }
        for slot in slots
    ]


@router.post("/{payment_id}/resolve")
async def resolve_payment_review(
    payment_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    try:
        payment, consultation = await PaymentReviewService(db).resolve(
            payment_id=payment_id,
            decision=payload.get("decision"),
            slot_id=payload.get("slot_id"),
            actor_id=actor_id_from_token(actor),
            comment=payload.get("comment") or "",
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (
        PaymentReviewResolutionError,
        SlotUnavailableError,
        ValueError,
    ) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise

    return {
        "ok": True,
        "payment_id": payment.id,
        "payment_status": payment.status,
        "consultation_id": consultation.id,
        "consultation_status": consultation.status,
        "slot_id": consultation.slot_id,
    }


@router.get("/ui", response_class=HTMLResponse)
async def payment_review_center_ui():
    return HTMLResponse(PAYMENT_REVIEW_CENTER_HTML)


PAYMENT_REVIEW_CENTER_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка полученных платежей</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}
header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center}
main{max-width:1400px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}
table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}
button{border:0;border-radius:9px;padding:9px 12px;color:#fff;font-weight:700;cursor:pointer;background:#2563eb}.green{background:#15803d}.red{background:#b91c1c}.gray{background:#4b5563}
select{max-width:360px;padding:8px;border:1px solid #d1d5db;border-radius:8px}.muted{color:#6b7280;font-size:13px}.reason{max-width:340px;white-space:normal}.notice{background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:12px;margin-bottom:16px}.actions{display:grid;gap:7px}
@media(max-width:900px){table{display:block;overflow-x:auto;font-size:12px}}
</style>
</head>
<body>
<header><b>⚖ Проверка полученных платежей</b><a href="/admin-ui" style="color:white">Админка</a></header>
<main>
<div class="notice"><b>Правило безопасности.</b> Здесь обрабатываются только деньги, которые провайдер подтвердил, но автоматическая бронь не состоялась. Нельзя вручную создавать новую оплату: администратор либо связывает полученные деньги со свободным слотом, либо направляет их в очередь возврата.</div>
<div class="card"><h2>PAID_REVIEW</h2><div id="content">Загрузка…</div></div>
<div id="message" class="muted"></div>
</main>
<script>
let token='';let slots=[];
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function formatDate(v){if(!v)return '—';return new Date(v).toLocaleString('ru-RU')}
function slotOptions(){return '<option value="">Выберите свободный слот</option>'+slots.map(s=>`<option value="${s.id}">${esc(formatDate(s.starts_at))} — ${esc(s.lawyer_name)}</option>`).join('')}
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;await load()}
async function load(){const data=await Promise.all([api('/admin/payment-reviews'),api('/admin/payment-reviews/slots')]);const rows=data[0];slots=data[1];content.innerHTML=rows.length?`<table><thead><tr><th>Платёж</th><th>Дело / клиент</th><th>Причина</th><th>Консультация</th><th>Решение</th></tr></thead><tbody>${rows.map(x=>`<tr><td><b>#${x.payment_id}</b><br>${x.amount.toLocaleString('ru-RU')} ${esc(x.currency)}<br><span class="muted">${esc(x.provider||'—')} ${esc(x.provider_payment_id||'')}</span></td><td><b>${esc(x.case_number||x.case_id)}</b><br>${esc(x.client_name||'—')}<br><span class="muted">TG ${esc(x.telegram_id||'—')}</span></td><td class="reason">${esc(x.reason||'Причина не сохранена')}<br><span class="muted">Ключ: ${esc(x.reservation_key||'—')}</span></td><td>${esc(x.consultation_status||'нет')}<br><span class="muted">Слот: ${esc(x.current_slot_id||'—')}<br>${esc(formatDate(x.scheduled_at))}</span></td><td><div class="actions">${x.existing_booking_valid?`<button class="green" onclick="resolveReview(${x.payment_id},'confirm_existing')">Подтвердить текущую бронь</button>`:''}<select id="slot_${x.payment_id}">${slotOptions()}</select><button onclick="resolveReview(${x.payment_id},'assign_slot')">Назначить выбранный слот</button><button class="red" onclick="resolveReview(${x.payment_id},'refund_pending')">Направить на возврат</button></div></td></tr>`).join('')}</tbody></table>`:'Платежей, требующих ручной проверки, нет.'}
async function resolveReview(id,decision){let slotId=null;if(decision==='assign_slot'){slotId=Number(document.getElementById('slot_'+id).value||0);if(!slotId){alert('Выберите свободный слот');return}}const question=decision==='refund_pending'?'Укажите причину направления на возврат:':'Укажите результат проверки:';const comment=prompt(question);if(!comment)return;try{await api('/admin/payment-reviews/'+id+'/resolve',{method:'POST',body:JSON.stringify({decision,slot_id:slotId,comment})});message.textContent='Решение сохранено';await load()}catch(e){message.textContent=e.message}}
boot();
</script>
</body>
</html>
"""
