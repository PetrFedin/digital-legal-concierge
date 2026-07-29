from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.consultations.no_show_resolution_service import (
    NoShowResolutionError,
    NoShowResolutionService,
)
from app.domain.consultations.outcome_service import (
    ConsultationOutcomeError,
    ConsultationOutcomeService,
)
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, decode_access_token, has_role

router = APIRouter(
    prefix="/admin/consultation-outcomes",
    tags=["admin", "consultation-outcomes"],
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


@router.get("")
async def list_outcome_queue(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    rows = (
        await db.execute(
            select(
                Consultation,
                Case,
                User,
                ConsultationSlot,
                Lawyer,
            )
            .join(Case, Case.id == Consultation.case_id)
            .join(User, User.id == Case.client_id)
            .join(
                ConsultationSlot,
                ConsultationSlot.id == Consultation.slot_id,
            )
            .join(Lawyer, Lawyer.id == Consultation.lawyer_id)
            .where(
                (
                    (Consultation.status == ConsultationStatus.BOOKED)
                    & (ConsultationSlot.starts_at <= cutoff)
                )
                | (
                    Consultation.status
                    == ConsultationStatus.LAWYER_NO_SHOW
                )
            )
            .order_by(ConsultationSlot.starts_at.asc())
        )
    ).all()
    return [
        {
            "consultation_id": consultation.id,
            "case_id": case.id,
            "case_number": case.case_number,
            "client_name": user.full_name,
            "client_telegram_id": user.telegram_id,
            "lawyer_id": lawyer.id,
            "lawyer_name": lawyer.full_name,
            "status": consultation.status,
            "slot_id": slot.id,
            "slot_status": slot.status,
            "starts_at": slot.starts_at.isoformat(),
            "ends_at": slot.ends_at.isoformat(),
            "next_action": case.next_action,
        }
        for consultation, case, user, slot, lawyer in rows
    ]


@router.get("/slots")
async def available_slots(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    slots = await SlotService(db).get_available_slots(limit=200)
    lawyer_ids = {slot.lawyer_id for slot in slots}
    lawyers = {
        row.id: row
        for row in (
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


@router.post("/{consultation_id}/lawyer-no-show")
async def mark_lawyer_no_show(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    try:
        consultation = await ConsultationOutcomeService(
            db
        ).mark_lawyer_no_show(
            consultation_id=consultation_id,
            admin_id=actor_id_from_token(actor),
            comment=payload.get("comment") or "",
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ConsultationOutcomeError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "consultation_id": consultation.id,
        "status": consultation.status,
    }


@router.post("/{consultation_id}/rebook")
async def rebook_after_lawyer_no_show(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    try:
        consultation = await ConsultationOutcomeService(
            db
        ).rebook_after_lawyer_no_show(
            consultation_id=consultation_id,
            new_slot_id=int(payload.get("slot_id") or 0),
            admin_id=actor_id_from_token(actor),
            comment=payload.get("comment") or "",
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (
        ConsultationOutcomeError,
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
        "consultation_id": consultation.id,
        "status": consultation.status,
        "slot_id": consultation.slot_id,
    }


@router.post("/{consultation_id}/refund")
async def refund_after_lawyer_no_show(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    try:
        consultation, payment = await NoShowResolutionService(
            db
        ).route_lawyer_no_show_to_refund(
            consultation_id=consultation_id,
            admin_id=actor_id_from_token(actor),
            comment=payload.get("comment") or "",
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NoShowResolutionError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "consultation_id": consultation.id,
        "consultation_status": consultation.status,
        "payment_id": payment.id,
        "payment_status": payment.status,
    }


@router.get("/ui", response_class=HTMLResponse)
async def consultation_outcomes_ui():
    return HTMLResponse(OUTCOMES_HTML)


OUTCOMES_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Контроль консультаций</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between}main{max-width:1400px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}.notice{background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:12px;margin-bottom:16px}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}button{border:0;border-radius:9px;padding:8px 11px;color:#fff;background:#2563eb;font-weight:700;cursor:pointer}.red{background:#b91c1c}.yellow{background:#ca8a04}.muted{font-size:13px;color:#6b7280}select{max-width:360px;padding:8px;border:1px solid #d1d5db;border-radius:8px}.actions{display:grid;gap:6px}@media(max-width:900px){table{display:block;overflow-x:auto;font-size:12px}}
</style>
</head>
<body>
<header><b>⚖ Контроль завершения консультаций</b><a href="/admin-ui" style="color:white">Админка</a></header>
<main><div class="notice"><b>Порядок.</b> Просроченная встреча должна быть закрыта юристом результатом или неявкой клиента. Если не явился юрист, администратор фиксирует нарушение и предлагает клиенту бесплатный перенос либо возврат.</div><div class="card"><h2>Требуют решения</h2><div id="content">Загрузка…</div></div><div id="message" class="muted"></div></main>
<script>
let token='';let slots=[];
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function dt(v){return v?new Date(v).toLocaleString('ru-RU'):'—'}
function slotOptions(){return '<option value="">Выберите новый слот</option>'+slots.map(s=>`<option value="${s.id}">${esc(dt(s.starts_at))} — ${esc(s.lawyer_name)}</option>`).join('')}
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;await load()}
async function load(){const data=await Promise.all([api('/admin/consultation-outcomes'),api('/admin/consultation-outcomes/slots')]);const rows=data[0];slots=data[1];content.innerHTML=rows.length?`<table><tr><th>Встреча</th><th>Дело / клиент</th><th>Юрист</th><th>Статус</th><th>Решение</th></tr>${rows.map(x=>`<tr><td>${esc(dt(x.starts_at))}<br><span class="muted">${esc(x.slot_status)}</span></td><td><b>${esc(x.case_number)}</b><br>${esc(x.client_name)}<br><span class="muted">TG ${esc(x.client_telegram_id)}</span></td><td>${esc(x.lawyer_name)}</td><td>${esc(x.status)}<br><span class="muted">${esc(x.next_action||'')}</span></td><td><div class="actions">${x.status==='BOOKED'?`<button class="yellow" onclick="markNoShow(${x.consultation_id})">Юрист не явился</button>`:`<select id="slot_${x.consultation_id}">${slotOptions()}</select><button onclick="rebook(${x.consultation_id})">Бесплатный перенос</button><button class="red" onclick="refund(${x.consultation_id})">На возврат</button>`}</div></td></tr>`).join('')}</table>`:'Просроченных консультаций нет.'}
async function markNoShow(id){const comment=prompt('Опишите обстоятельства неявки юриста:');if(!comment)return;try{await api('/admin/consultation-outcomes/'+id+'/lawyer-no-show',{method:'POST',body:JSON.stringify({comment})});message.textContent='Неявка юриста зафиксирована';await load()}catch(e){message.textContent=e.message}}
async function rebook(id){const slotId=Number(document.getElementById('slot_'+id).value||0);if(!slotId){alert('Выберите слот');return}const comment=prompt('Комментарий к бесплатному переносу:');if(!comment)return;try{await api('/admin/consultation-outcomes/'+id+'/rebook',{method:'POST',body:JSON.stringify({slot_id:slotId,comment})});message.textContent='Консультация перенесена без повторной оплаты';await load()}catch(e){message.textContent=e.message}}
async function refund(id){const comment=prompt('Причина направления платежа на возврат:');if(!comment)return;try{await api('/admin/consultation-outcomes/'+id+'/refund',{method:'POST',body:JSON.stringify({comment})});message.textContent='Платёж направлен в очередь возврата';await load()}catch(e){message.textContent=e.message}}
boot();
</script>
</body>
</html>
"""
