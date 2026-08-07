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

CONSULTATION_STATUS_LABELS = {
    ConsultationStatus.BOOKED: "Ожидает результата встречи",
    ConsultationStatus.LAWYER_NO_SHOW: "Неявка юриста зафиксирована",
}
SLOT_STATUS_LABELS = {
    "available": "Свободен",
    "free": "Свободен",
    "booked": "Забронирован",
    "reserved": "Временно зарезервирован",
    "lawyer_no_show": "Неявка юриста",
    "client_no_show": "Неявка клиента",
    "completed": "Встреча завершена",
    "cancelled": "Недоступен",
}


def _consultation_status_label(value: str | None) -> str:
    return CONSULTATION_STATUS_LABELS.get(
        str(value or ""),
        "Статус консультации уточняется",
    )


def _slot_status_label(value: str | None) -> str:
    return SLOT_STATUS_LABELS.get(
        str(value or "").lower(),
        "Статус времени уточняется",
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
            "status_label": _consultation_status_label(consultation.status),
            "slot_id": slot.id,
            "slot_status": slot.status,
            "slot_status_label": _slot_status_label(slot.status),
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
    lawyers = (
        {
            row.id: row
            for row in (
                await db.execute(
                    select(Lawyer).where(Lawyer.id.in_(lawyer_ids))
                )
            ).scalars().all()
        }
        if lawyer_ids
        else {}
    )
    return [
        {
            "id": slot.id,
            "lawyer_id": slot.lawyer_id,
            "lawyer_name": (
                lawyers[slot.lawyer_id].full_name
                if slot.lawyer_id in lawyers
                else "Юрист не указан"
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
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--green:#14804a;--red:#b42318;--amber:#a15c00;--blue2:#eef2ff;--green2:#ecfdf3;--red2:#fef3f2;--amber2:#fff7e6;--shadow:0 12px 30px rgba(16,24,40,.07)}
*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:var(--bg);margin:0;color:var(--ink)}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px}.header{max-width:1320px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:16px}.header h1{margin:0 0 4px;font-size:22px}.header p{margin:0;color:#d0d5dd;font-size:13px}.links,.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}a.button,button{border:0;border-radius:10px;padding:9px 12px;color:#fff;background:var(--blue);font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}button:disabled,select:disabled,textarea:disabled{opacity:.55;cursor:wait}.secondary{background:#475467!important}.green{background:var(--green)!important}.red{background:var(--red)!important}.amber{background:var(--amber)!important}main{max-width:1320px;margin:auto;padding:22px}.notice{background:var(--amber2);border:1px solid #fedf89;border-radius:13px;padding:13px;margin-bottom:14px;line-height:1.45}.feedback{min-height:24px;margin:0 0 10px;font-size:13px}.feedback.ok{color:var(--green)}.feedback.bad{color:var(--red)}.feedback.warn{color:var(--amber)}.feedback.muted{color:var(--muted)}.panel,.case{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}.panel{padding:16px}.panel-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-end;margin-bottom:13px}.panel-head h2{margin:0 0 4px;font-size:19px}.muted{font-size:13px;color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.case{padding:15px}.case-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.case h3{margin:0 0 4px;font-size:18px}.badge{display:inline-flex;border-radius:999px;padding:5px 9px;background:var(--amber2);color:var(--amber);font-size:12px;font-weight:750}.badge.red{background:var(--red2);color:var(--red)}.meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin:12px 0}.cell{background:#f8fafc;border-radius:10px;padding:10px}.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.next{background:var(--blue2);border:1px solid #c7d2fe;border-radius:11px;padding:11px;line-height:1.4;margin:10px 0}.actions{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}.form{display:none;border-top:1px solid var(--line);margin-top:12px;padding-top:12px}.form.open{display:block}.form textarea{width:100%;min-height:96px;border:1px solid #d0d5dd;border-radius:10px;padding:10px;resize:vertical;margin:7px 0}.form select{width:100%;padding:9px;border:1px solid #d0d5dd;border-radius:9px;margin:7px 0}.hint,.rule{font-size:12px;color:var(--muted);line-height:1.45}.rule{margin:7px 0}.rule b{color:var(--ink)}.empty,.error,.loading{padding:30px;text-align:center;border:1px dashed var(--line);border-radius:14px;background:#fff;color:var(--muted)}.error{background:var(--red2);color:var(--red)}.empty h3,.error h3{margin-top:0}.choice{display:grid;grid-template-columns:1fr 1fr;gap:8px}.choice button{width:100%}
@media(max-width:900px){.cards{grid-template-columns:1fr}.header,.panel-head{align-items:flex-start;flex-direction:column}.meta{grid-template-columns:1fr 1fr}}
@media(max-width:520px){main{padding:12px}.meta,.choice{grid-template-columns:1fr}.links,.row{width:100%;align-items:stretch;flex-direction:column}a.button,button{width:100%;text-align:center}}
</style>
</head>
<body>
<header><div class="header"><div><h1>⚖ Контроль консультаций</h1><p>Неявка юриста, бесплатный перенос или возврат — по одному понятному решению за раз.</p></div><div class="links"><a class="button secondary" href="/admin/workdesk/ui">Рабочий стол</a><a class="button secondary" href="/operator">Все разделы</a></div></div></header>
<main>
<div class="notice"><b>Когда появляется действие.</b> Подтверждённая встреча попадает сюда через 15 минут после начала, если результат ещё не зафиксирован. Неявку отмечайте только после проверки фактов. После неявки обязательно завершите ситуацию бесплатным переносом или маршрутом возврата.</div>
<div id="message" class="feedback muted" role="status" aria-live="polite">Загрузка очереди…</div>
<section class="panel"><div class="panel-head"><div><h2>Требуют решения</h2><div class="muted">Внутренние номера используются только для безопасного запроса и не показываются сотруднику как рабочий контекст.</div></div><button class="secondary" onclick="reload(this)">Обновить</button></div><div id="content"><div class="loading">Загрузка консультаций…</div></div></section>
</main>
<script>
let token='',slots=[];const pendingConsultations=new Set(),drafts=new Map(),selectedSlots=new Map();let rowsById=new Map();
const consultationLabels={BOOKED:'Ожидает результата встречи',LAWYER_NO_SHOW:'Неявка юриста зафиксирована',DONE:'Консультация завершена',CLIENT_NO_SHOW:'Клиент не явился',RESCHEDULED:'Консультация перенесена',CANCELLED:'Консультация отменена',CLOSED:'Консультация закрыта'};
const slotLabels={available:'Свободен',free:'Свободен',booked:'Забронирован',reserved:'Временно зарезервирован',lawyer_no_show:'Неявка юриста',client_no_show:'Неявка клиента',completed:'Встреча завершена',cancelled:'Недоступен'};
const paymentLabels={REFUND_PENDING:'Возврат поставлен в очередь',REFUND_DECLINED:'Возврат отклонён',REFUNDED:'Возврат выполнен',PAID:'Оплачено',FAILED:'Операция завершилась ошибкой',CANCELLED:'Операция отменена'};
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function dt(v){return v?new Date(v).toLocaleString('ru-RU'):'—'}
function feedback(text,state='muted'){message.textContent=text;message.className='feedback '+state}
function consultationLabel(x){return x.status_label||consultationLabels[String(x.status||'')]||'Статус консультации уточняется'}
function slotLabel(x){return x.slot_status_label||slotLabels[String(x.slot_status||'').toLowerCase()]||'Статус времени уточняется'}
function draftKey(id,mode){return `${id}:${mode}`}
function rememberDraft(id,mode,value){drafts.set(draftKey(id,mode),value)}
function rememberSlot(id,value){selectedSlots.set(Number(id),String(value||''))}
function consultationControls(id){return Array.from(document.querySelectorAll(`[data-consultation-id="${id}"]`))}
function slotOptions(id){const selected=selectedSlots.get(Number(id))||'';if(!slots.length)return '<option value="">Свободных слотов сейчас нет</option>';return '<option value="">Выберите новое время</option>'+slots.map(s=>`<option value="${s.id}" ${String(s.id)===selected?'selected':''}>${esc(dt(s.starts_at))} — ${esc(s.lawyer_name||'Юрист не указан')}</option>`).join('')}
async function api(path,opts={}){if(!token)throw new Error('Персональная сессия не загружена');const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';throw new Error('Сессия истекла или недостаточно прав')}const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка запроса');return d}
function card(x){const id=Number(x.consultation_id),caseId=Number(x.case_id),isNoShow=x.status==='LAWYER_NO_SHOW',noShowDraft=drafts.get(draftKey(id,'no_show'))||'',rebookDraft=drafts.get(draftKey(id,'rebook'))||'',refundDraft=drafts.get(draftKey(id,'refund'))||'';return `<article class="case"><div class="case-head"><div><h3>${esc(x.case_number)}</h3><div class="muted">${esc(x.client_name)} · ${esc(x.lawyer_name)}</div></div><span class="badge ${isNoShow?'red':''}">${esc(consultationLabel(x))}</span></div><div class="meta"><div class="cell"><span>Начало</span>${esc(dt(x.starts_at))}</div><div class="cell"><span>Окончание</span>${esc(dt(x.ends_at))}</div><div class="cell"><span>Статус времени</span>${esc(slotLabel(x))}</div><div class="cell"><span>Ответственный</span>${esc(x.lawyer_name)}</div></div><div class="next"><b>Следующий шаг</b><br>${esc(x.next_action||'Проверить результат консультации и определить дальнейшее действие')}</div><div class="actions">${!isNoShow?`<div class="row"><button data-consultation-id="${id}" class="amber" onclick="openForm(${id},'no_show')">Зафиксировать неявку юриста</button><a class="button secondary" href="/admin/workdesk/cases/${caseId}/action/consultation">Открыть дело</a><a class="button secondary" href="/message-center/ui?case_id=${caseId}">Переписка</a></div><div id="form_${id}_no_show" class="form"><b>Проверка перед фиксацией неявки</b><textarea id="comment_${id}_no_show" oninput="rememberDraft(${id},'no_show',this.value)" placeholder="Что произошло и как это проверено">${esc(noShowDraft)}</textarea><div class="hint">Минимум 5 символов. После сохранения нужно выбрать бесплатный перенос или возврат.</div><div class="rule"><b>Ничего не изменится</b>, пока вы не подтвердите действие.</div><div class="row"><button data-consultation-id="${id}" class="amber" onclick="markNoShow(${id},this)">Подтвердить неявку</button><button class="secondary" onclick="closeForm(${id},'no_show')">Вернуться без сохранения</button></div></div>`:`<div class="choice"><button data-consultation-id="${id}" onclick="openForm(${id},'rebook')">Бесплатный перенос</button><button data-consultation-id="${id}" class="red" onclick="openForm(${id},'refund')">Направить на возврат</button></div><div class="row" style="margin-top:8px"><a class="button secondary" href="/admin/workdesk/cases/${caseId}/action/consultation">Открыть дело</a><a class="button secondary" href="/message-center/ui?case_id=${caseId}">Переписка</a></div><div id="form_${id}_rebook" class="form"><b>Бесплатный перенос</b><select data-consultation-id="${id}" id="slot_${id}" onchange="rememberSlot(${id},this.value)">${slotOptions(id)}</select><textarea id="comment_${id}_rebook" oninput="rememberDraft(${id},'rebook',this.value)" placeholder="Что согласовано с клиентом и почему выбран перенос">${esc(rebookDraft)}</textarea><div class="hint">Минимум 5 символов. Клиент не оплачивает консультацию повторно.</div><div class="rule"><b>Ничего не изменится</b>, пока вы не подтвердите перенос.</div><div class="row"><button data-consultation-id="${id}" class="green" onclick="rebook(${id},this)">Подтвердить перенос</button><button class="secondary" onclick="closeForm(${id},'rebook')">Вернуться без сохранения</button></div></div><div id="form_${id}_refund" class="form"><b>Маршрут возврата</b><textarea id="comment_${id}_refund" oninput="rememberDraft(${id},'refund',this.value)" placeholder="Почему выбран возврат вместо переноса">${esc(refundDraft)}</textarea><div class="hint">Минимум 5 символов. Это создаёт маршрут возврата; банковское перечисление выполняется отдельным процессом.</div><div class="rule"><b>Ничего не изменится</b>, пока вы не подтвердите направление на возврат.</div><div class="row"><button data-consultation-id="${id}" class="red" onclick="refund(${id},this)">Подтвердить возврат</button><button class="secondary" onclick="closeForm(${id},'refund')">Вернуться без сохранения</button></div></div>`}</div></article>`}
function render(rows){rowsById=new Map(rows.map(x=>[Number(x.consultation_id),x]));content.innerHTML=rows.length?`<div class="cards">${rows.map(card).join('')}</div>`:`<div class="empty"><h3>Незавершённых решений нет</h3><p>Просроченных консультаций и неявок юриста, требующих решения, сейчас нет.</p><div class="row" style="justify-content:center"><a class="button" href="/admin/workdesk/ui">Рабочий стол</a><a class="button secondary" href="/operator">Все разделы</a></div></div>`}
function openForm(id,mode){document.querySelectorAll(`[id^="form_${id}_"]`).forEach(x=>x.classList.remove('open'));const form=document.getElementById(`form_${id}_${mode}`);if(!form){feedback('Действие уже изменилось. Обновите список.','bad');return}form.classList.add('open');form.querySelector('textarea,select')?.focus()}
function closeForm(id,mode){document.getElementById(`form_${id}_${mode}`)?.classList.remove('open')}
function validComment(comment){if(!comment)return false;if(comment.trim().length<5){feedback('Комментарий должен содержать не менее 5 символов. Черновик сохранён.','bad');return false}return true}
async function withConsultationAction(id,button,work){if(pendingConsultations.has(id))return;pendingConsultations.add(id);const controls=consultationControls(id);const labels=new Map(controls.filter(x=>x.tagName==='BUTTON').map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Сохранение…';try{return await work()}finally{pendingConsultations.delete(id);controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>x.textContent=label)}}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();const roles=s.roles||[s.role];if(!roles.includes('admin')){location.href='/operator';return}token=s.api_token||'';try{await load()}catch(e){content.innerHTML=`<div class="error"><h3>Контроль консультаций не загружен</h3><p>${esc(e.message)}</p><div class="row" style="justify-content:center"><button onclick="load().catch(x=>feedback(x.message,'bad'))">Повторить</button><a class="button secondary" href="/admin/workdesk/ui">Рабочий стол</a></div></div>`;feedback(e.message,'bad')}}
async function load(){const data=await Promise.all([api('/admin/consultation-outcomes'),api('/admin/consultation-outcomes/slots')]);slots=data[1]||[];render(data[0]||[]);feedback((data[0]||[]).length?'Показаны консультации, требующие решения.':'Очередь обработана.','muted')}
async function reload(button){button.disabled=true;const old=button.textContent;button.textContent='Обновление…';try{await load()}catch(e){feedback('Список не обновлён: '+e.message,'bad')}finally{button.disabled=false;button.textContent=old}}
async function markNoShow(id,button){const row=rowsById.get(Number(id));const comment=document.getElementById(`comment_${id}_no_show`)?.value||'';if(!row){feedback('Консультация уже изменилась. Обновите список.','bad');return}if(!validComment(comment))return;if(!confirm(`Подтвердить неявку юриста по делу ${row.case_number}? После фиксации потребуется выбрать бесплатный перенос или возврат.`))return;return withConsultationAction(id,button,async()=>{try{await api('/admin/consultation-outcomes/'+id+'/lawyer-no-show',{method:'POST',body:JSON.stringify({comment:comment.trim()})});drafts.delete(draftKey(id,'no_show'));feedback(`Неявка юриста по делу ${row.case_number} зафиксирована.`,'ok');try{await load()}catch(e){feedback(`Неявка сохранена, но список не обновился: ${e.message}`,'warn')}}catch(e){feedback(`Неявка по делу ${row.case_number} не сохранена: ${e.message}. Черновик остаётся на экране.`,'bad')}})}
async function rebook(id,button){const row=rowsById.get(Number(id));const slotId=Number(document.getElementById('slot_'+id)?.value||0);const comment=document.getElementById(`comment_${id}_rebook`)?.value||'';if(!row){feedback('Консультация уже изменилась. Обновите список.','bad');return}if(!slotId){feedback(slots.length?'Выберите новое свободное время.':'Свободных слотов сейчас нет. Свяжитесь с клиентом и вернитесь позже.','bad');return}if(!validComment(comment))return;if(!confirm(`Подтвердить бесплатный перенос по делу ${row.case_number}? Повторная оплата с клиента не взимается.`))return;return withConsultationAction(id,button,async()=>{try{await api('/admin/consultation-outcomes/'+id+'/rebook',{method:'POST',body:JSON.stringify({slot_id:slotId,comment:comment.trim()})});drafts.delete(draftKey(id,'rebook'));selectedSlots.delete(Number(id));feedback(`Консультация по делу ${row.case_number} перенесена без повторной оплаты.`,'ok');try{await load()}catch(e){feedback(`Перенос сохранён, но список не обновился: ${e.message}`,'warn')}}catch(e){feedback(`Перенос по делу ${row.case_number} не сохранён: ${e.message}. Черновик остаётся на экране.`,'bad')}})}
async function refund(id,button){const row=rowsById.get(Number(id));const comment=document.getElementById(`comment_${id}_refund`)?.value||'';if(!row){feedback('Консультация уже изменилась. Обновите список.','bad');return}if(!validComment(comment))return;if(!confirm(`Направить оплату консультации по делу ${row.case_number} в очередь возврата? Это не выполняет банковский возврат автоматически.`))return;return withConsultationAction(id,button,async()=>{try{const result=await api('/admin/consultation-outcomes/'+id+'/refund',{method:'POST',body:JSON.stringify({comment:comment.trim()})});drafts.delete(draftKey(id,'refund'));const status=paymentLabels[String(result.payment_status||'')]||'Возврат требует проверки';feedback(`Оплата по делу ${row.case_number} направлена в очередь возврата. Статус: ${status}.`,'ok');try{await load()}catch(e){feedback(`Направление на возврат сохранено, но список не обновился: ${e.message}`,'warn')}}catch(e){feedback(`Направление по делу ${row.case_number} на возврат не сохранено: ${e.message}. Черновик остаётся на экране.`,'bad')}})}
boot();
</script>
</body>
</html>
"""
