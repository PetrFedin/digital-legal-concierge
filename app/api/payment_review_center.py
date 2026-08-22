from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.payments.orphan_payment_review_service import (
    OrphanPaymentReviewResolutionError,
    OrphanPaymentReviewService,
)
from app.domain.payments.payment_review_service import (
    PaymentReviewResolutionError,
    PaymentReviewService,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus
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

INACTIVE_REVIEW_ORIGINS = {
    str(PaymentStatus.EXPIRED),
    str(PaymentStatus.CANCELLED),
    str(PaymentStatus.FAILED),
}
TERMINAL_CONSULTATION_STATUSES = {
    str(ConsultationStatus.DONE),
    str(ConsultationStatus.CANCELLED),
    str(ConsultationStatus.CLOSED),
}


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


async def review_event_context(
    db: AsyncSession,
    *,
    case_id: int,
    payment_id: int,
) -> dict:
    events = list(
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == "CONSULTATION_PAYMENT_REVIEW_REQUIRED",
                )
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(50)
            )
        ).scalars().all()
    )
    for event in events:
        new_value = event.new_value or {}
        try:
            event_payment_id = int(new_value.get("payment_id") or 0)
        except (TypeError, ValueError):
            event_payment_id = 0
        if event_payment_id != int(payment_id):
            continue
        old_value = event.old_value or {}
        return {
            "reason": new_value.get("reason"),
            "origin_status": str(old_value.get("status") or "") or None,
            "review_created_at": (
                event.created_at.isoformat() if event.created_at else None
            ),
        }
    return {
        "reason": None,
        "origin_status": None,
        "review_created_at": None,
    }


def allowed_actions_for_candidate(
    candidate: dict,
    *,
    origin_status: str | None,
) -> list[str]:
    if candidate.get("existing_booking_valid"):
        if origin_status in INACTIVE_REVIEW_ORIGINS:
            return ["refund_pending"]
        return ["confirm_existing", "refund_pending"]
    if str(candidate.get("status") or "") in TERMINAL_CONSULTATION_STATUSES:
        return ["refund_pending"]
    return ["assign_slot", "refund_pending"]


def recommended_action(
    candidate: dict | None,
    *,
    origin_status: str | None,
    requires_selection: bool,
    orphan_refund_allowed: bool = False,
) -> str:
    if orphan_refund_allowed:
        return (
            "Привязка платежа указывает на отсутствующую консультацию. "
            "Не восстанавливайте её вручную: безопасно направьте только этот платёж на возврат. "
            "Текущее дело и существующие записи не изменятся."
        )
    if requires_selection:
        return (
            "Сначала сопоставьте старый платёж с нужной консультацией по истории дела. "
            "Без явного выбора система ничего не изменит."
        )
    if not candidate:
        return "Контекст консультации не найден. Проверьте историю дела до любого решения."
    if candidate.get("existing_booking_valid") and origin_status in INACTIVE_REVIEW_ORIGINS:
        return (
            "Платёж пришёл по ранее закрытой/истёкшей ссылке, а запись уже подтверждена. "
            "Направьте лишний платёж на возврат — текущая консультация будет сохранена."
        )
    if candidate.get("existing_booking_valid"):
        return (
            "Подтверждённая бронь цела. Если платёж действительно относится к этой записи, "
            "подтвердите связь; иначе направьте платёж на возврат."
        )
    if str(candidate.get("status") or "") in TERMINAL_CONSULTATION_STATUSES:
        return "Закрытую консультацию не восстанавливайте автоматически; используйте возврат."
    return (
        "Если деньги относятся к этой консультации, назначьте свободный слот без второй оплаты; "
        "иначе направьте платёж на возврат."
    )


async def consultation_candidates(
    db: AsyncSession,
    *,
    case_id: int,
    origin_status: str | None,
) -> list[dict]:
    consultations = list(
        (
            await db.execute(
                select(Consultation)
                .where(Consultation.case_id == case_id)
                .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            )
        ).scalars().all()
    )
    slots = SlotService(db)
    result: list[dict] = []
    for consultation in consultations:
        existing_booking_valid = False
        if consultation.slot_id:
            slot = await slots.get_slot(consultation.slot_id)
            existing_booking_valid = bool(
                slot
                and slot.status == "booked"
                and slot.consultation_id == consultation.id
            )
        item = {
            "id": consultation.id,
            "status": consultation.status,
            "slot_id": consultation.slot_id,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
            "existing_booking_valid": existing_booking_valid,
        }
        item["allowed_actions"] = allowed_actions_for_candidate(
            item,
            origin_status=origin_status,
        )
        result.append(item)
    return result


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
        review = await review_event_context(
            db,
            case_id=payment.case_id,
            payment_id=payment.id,
        )
        candidates = (
            await consultation_candidates(
                db,
                case_id=case.id,
                origin_status=review["origin_status"],
            )
            if case
            else []
        )
        linked_consultation_id, linked_slot_id = (
            PaymentReviewService.reservation_context(payment)
        )
        candidate = None
        context_source = "missing"
        if linked_consultation_id is not None:
            candidate = next(
                (
                    item
                    for item in candidates
                    if int(item["id"]) == int(linked_consultation_id)
                ),
                None,
            )
            context_source = (
                "reservation_key" if candidate else "broken_reservation_key"
            )
        elif len(candidates) == 1:
            candidate = candidates[0]
            context_source = "legacy_single_consultation"
        elif len(candidates) > 1:
            context_source = "legacy_manual_selection"

        requires_selection = bool(
            linked_consultation_id is None and len(candidates) > 1
        )
        orphan_refund_allowed = bool(
            case
            and linked_consultation_id is not None
            and candidate is None
            and context_source == "broken_reservation_key"
        )
        allowed_actions = candidate.get("allowed_actions", []) if candidate else []
        if orphan_refund_allowed:
            allowed_actions = ["refund_orphan"]

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
                "reservation_slot_id": linked_slot_id,
                "status": payment.status,
                "reason": review["reason"],
                "origin_status": review["origin_status"],
                "review_created_at": review["review_created_at"],
                "context_source": context_source,
                "requires_consultation_selection": requires_selection,
                "orphan_refund_allowed": orphan_refund_allowed,
                "consultation_id": candidate.get("id") if candidate else None,
                "consultation_status": candidate.get("status") if candidate else None,
                "current_slot_id": candidate.get("slot_id") if candidate else None,
                "scheduled_at": candidate.get("scheduled_at") if candidate else None,
                "existing_booking_valid": bool(
                    candidate and candidate.get("existing_booking_valid")
                ),
                "allowed_actions": allowed_actions,
                "consultation_candidates": candidates,
                "recommended_action": recommended_action(
                    candidate,
                    origin_status=review["origin_status"],
                    requires_selection=requires_selection,
                    orphan_refund_allowed=orphan_refund_allowed,
                ),
                "case_detail_url": (
                    f"/admin/cases/{case.id}/ui" if case else None
                ),
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
    lawyers = (
        {
            lawyer.id: lawyer
            for lawyer in (
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
    decision = str(payload.get("decision") or "").strip().lower()
    consultation = None
    try:
        if decision == "refund_orphan":
            payment = await OrphanPaymentReviewService(db).route_to_refund(
                payment_id=payment_id,
                actor_id=actor_id_from_token(actor),
                comment=payload.get("comment") or "",
            )
        else:
            payment, consultation = await PaymentReviewService(db).resolve(
                payment_id=payment_id,
                decision=decision,
                slot_id=payload.get("slot_id"),
                consultation_id=payload.get("consultation_id"),
                actor_id=actor_id_from_token(actor),
                comment=payload.get("comment") or "",
            )
        # Presentation/API response values are copied before commit. This route
        # must remain correct if the session policy changes to expire_on_commit
        # and must never trigger implicit ORM I/O while serializing the response.
        response = {
            "ok": True,
            "payment_id": int(payment.id),
            "case_id": int(payment.case_id),
            "payment_status": str(payment.status),
            "consultation_id": int(consultation.id) if consultation else None,
            "consultation_status": str(consultation.status) if consultation else None,
            "slot_id": (
                int(consultation.slot_id)
                if consultation and consultation.slot_id is not None
                else None
            ),
        }
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (
        OrphanPaymentReviewResolutionError,
        PaymentReviewResolutionError,
        SlotUnavailableError,
        ValueError,
    ) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise

    return response


@router.get("/ui", response_class=HTMLResponse)
async def payment_review_center_ui():
    return HTMLResponse(PAYMENT_REVIEW_CENTER_HTML)


PAYMENT_REVIEW_CENTER_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Сверка полученных платежей</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--blue-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fef3f2;--amber:#a15c00;--amber-soft:#fff7e6}*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:var(--bg);margin:0;color:var(--ink)}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px}header .inner{max-width:1180px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:14px}.nav{display:flex;gap:10px;flex-wrap:wrap}header a{color:white}main{max-width:1180px;margin:auto;padding:22px}.notice{background:var(--blue-soft);border:1px solid #c7d2fe;border-radius:14px;padding:13px;margin-bottom:14px}.review{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:14px;box-shadow:0 10px 28px rgba(16,24,40,.05)}.review-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.review h2{font-size:18px;margin:0 0 4px}.badge{display:inline-flex;border-radius:999px;padding:5px 9px;background:var(--amber-soft);color:var(--amber);font-size:12px;font-weight:800}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:12px 0}.cell{background:#f8fafc;border-radius:11px;padding:10px}.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:4px}.reason,.next{border-radius:12px;padding:11px;margin:9px 0;line-height:1.4}.reason{background:var(--amber-soft);border:1px solid #fedf89}.next{background:var(--blue-soft);border:1px solid #c7d2fe}.actions{display:grid;gap:8px;margin-top:10px;max-width:620px}button,.button{border:0;border-radius:10px;padding:10px 12px;color:#fff;font-weight:750;cursor:pointer;background:var(--blue);text-decoration:none;display:inline-block;text-align:center}button.green{background:var(--green)}button.red{background:var(--red)}button.gray,.button.gray{background:#475467}button:disabled,select:disabled{opacity:.55;cursor:wait}select{width:100%;padding:10px;border:1px solid #d0d5dd;border-radius:10px;background:#fff}.muted{color:var(--muted);font-size:12px}.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--amber)}.empty{background:#fff;border:1px dashed var(--line);border-radius:16px;padding:28px;text-align:center;color:var(--muted)}.empty .button{margin:12px 4px 0}@media(max-width:760px){header .inner,.review-head{flex-direction:column}.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><div class="inner"><div><b id="pageTitle">⚖ Сверка полученных платежей</b><div style="font-size:12px;color:#d0d5dd">Деньги получены, но автоматическое действие остановлено безопасностью</div></div><div class="nav"><a href="/admin/workdesk/ui">Рабочий стол</a><a href="/admin/payment-reviews/ui">Вся очередь</a><a href="/admin-ui">Админка</a></div></div></header>
<main>
<div class="notice"><b>Правило.</b> Здесь нет ручной смены статуса дела. Сначала определяется точная консультация, затем деньги либо связываются с корректной записью, либо идут в контролируемый возврат. Поздний лишний платёж не отменяет уже подтверждённую консультацию.</div>
<div id="content">Загрузка…</div>
<div id="message" class="muted" role="status" aria-live="polite"></div>
</main>
<script>
const params=new URLSearchParams(location.search),requestedPaymentId=Number(params.get('payment_id')||0),requestedCaseId=Number(params.get('case_id')||0);let token='',slots=[],rowsById=new Map(),terminalCaseId=requestedCaseId||0;const pendingPayments=new Set();
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';throw new Error('Сессия истекла или недостаточно прав')}const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
function feedback(text,state='ok'){message.textContent=text;message.className='muted '+state}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function formatDate(v){if(!v)return '—';return new Date(v).toLocaleString('ru-RU')}
function slotOptions(){return '<option value="">Выберите свободный слот</option>'+slots.map(s=>`<option value="${s.id}">${esc(formatDate(s.starts_at))} — ${esc(s.lawyer_name)}</option>`).join('')}
function contextOptions(x){return '<option value="">Выберите консультацию после сверки</option>'+(x.consultation_candidates||[]).map(c=>`<option value="${c.id}">#${c.id} · ${esc(c.status)} · ${esc(formatDate(c.scheduled_at))}</option>`).join('')}
function selectedCandidate(x){if(!x.requires_consultation_selection)return (x.consultation_candidates||[]).find(c=>Number(c.id)===Number(x.consultation_id))||null;const el=document.getElementById('context_'+x.payment_id);const id=Number(el?.value||0);return (x.consultation_candidates||[]).find(c=>Number(c.id)===id)||null}
function actionButtons(x,c){const rowActions=x.allowed_actions||[];if(!c){if(rowActions.includes('refund_orphan'))return `<button class="red" data-payment-id="${x.payment_id}" onclick="resolveReview(${x.payment_id},'refund_orphan',this)">Вернуть платёж без изменения дела</button>`;return '<div class="muted">Выберите консультацию — до этого действия заблокированы.</div>'}const actions=c.allowed_actions||[];let html='';if(actions.includes('confirm_existing'))html+=`<button class="green" data-payment-id="${x.payment_id}" onclick="resolveReview(${x.payment_id},'confirm_existing',this)">Подтвердить связь с текущей бронью</button>`;if(actions.includes('assign_slot'))html+=`<select data-payment-id="${x.payment_id}" id="slot_${x.payment_id}">${slotOptions()}</select><button data-payment-id="${x.payment_id}" onclick="resolveReview(${x.payment_id},'assign_slot',this)">Назначить выбранный слот без второй оплаты</button>`;if(actions.includes('refund_pending')){const label=c.existing_booking_valid?'Вернуть этот платёж, запись сохранить':'Направить платёж на возврат';html+=`<button class="red" data-payment-id="${x.payment_id}" onclick="resolveReview(${x.payment_id},'refund_pending',this)">${label}</button>`}return html||'<div class="muted">Для выбранного контекста автоматических действий нет. Откройте карточку дела и сверку истории.</div>'}
function renderActions(id){const x=rowsById.get(Number(id));if(!x)return;const node=document.getElementById('actions_'+id);if(!node)return;node.innerHTML=actionButtons(x,selectedCandidate(x))}
function reviewCard(x){const selection=x.requires_consultation_selection?`<select id="context_${x.payment_id}" onchange="renderActions(${x.payment_id})">${contextOptions(x)}</select>`:(x.orphan_refund_allowed?'<div class="bad"><b>Связанная консультация отсутствует.</b> Восстановление или подмена другой консультацией заблокированы.</div>':`<div><b>Консультация #${esc(x.consultation_id||'—')}</b> · ${esc(x.consultation_status||'контекст не найден')}<div class="muted">Слот ${esc(x.current_slot_id||'—')} · ${esc(formatDate(x.scheduled_at))}</div></div>`);return `<article class="review" id="payment-review-${x.payment_id}"><div class="review-head"><div><h2>Платёж #${x.payment_id} · ${x.amount.toLocaleString('ru-RU')} ${esc(x.currency)}</h2><div class="muted">Дело ${esc(x.case_number||x.case_id)} · ${esc(x.client_name||'Клиент')} · TG ${esc(x.telegram_id||'—')}</div></div><span class="badge">Требуется сверка</span></div><div class="grid"><div class="cell"><span>Провайдер</span>${esc(x.provider||'—')}<div class="muted">${esc(x.provider_payment_id||'')}</div></div><div class="cell"><span>Исходный статус ссылки</span>${esc(x.origin_status||'не сохранён')}</div><div class="cell"><span>Контекст</span>${esc(x.context_source)}</div></div><div class="reason"><b>Почему автоматика остановилась</b><br>${esc(x.reason||'Причина не сохранена; требуется сверка истории.')}<div class="muted">reservation: ${esc(x.reservation_key||'нет')}</div></div><div class="next"><b>Что делать</b><br>${esc(x.recommended_action)}</div><div>${selection}</div><div class="actions" id="actions_${x.payment_id}"></div><div style="margin-top:10px">${x.case_detail_url?`<a class="button gray" href="${esc(x.case_detail_url)}">Открыть карточку дела</a>`:''}</div></article>`}
function visibleRows(rows){return requestedPaymentId?rows.filter(x=>Number(x.payment_id)===requestedPaymentId):rows}
function emptyState(){if(requestedPaymentId){const back=terminalCaseId?`<a class="button" href="/admin/cases/${terminalCaseId}/ui">Вернуться в дело</a>`:'';return `<div class="empty"><b>Платёж #${requestedPaymentId} больше не требует сверки.</b><br><span>Он уже обработан, сменил статус или недоступен в этой очереди.</span><br>${back}<a class="button gray" href="/admin/payment-reviews/ui">Открыть всю очередь</a></div>`}return '<div class="empty">Платежей, требующих сверки, нет.</div>'}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;if(requestedPaymentId)pageTitle.textContent=`⚖ Сверка платежа #${requestedPaymentId}`;try{await load()}catch(e){feedback(e.message,'bad')}}
async function load(){const data=await Promise.all([api('/admin/payment-reviews'),api('/admin/payment-reviews/slots')]);const rows=data[0];slots=data[1];rowsById=new Map(rows.map(x=>[Number(x.payment_id),x]));const visible=visibleRows(rows);if(requestedPaymentId&&visible.length){terminalCaseId=Number(visible[0].case_id)||terminalCaseId}content.innerHTML=visible.length?visible.map(reviewCard).join(''):emptyState();visible.forEach(x=>renderActions(x.payment_id))}
function paymentControls(id){return Array.from(document.querySelectorAll(`[data-payment-id="${id}"]`))}
async function withPaymentAction(id,button,work){if(pendingPayments.has(id))return;pendingPayments.add(id);const controls=paymentControls(id),labels=new Map(controls.filter(x=>x.tagName==='BUTTON').map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Выполняется…';try{return await work()}finally{pendingPayments.delete(id);controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>x.textContent=label)}}
function decisionLabel(decision){return decision==='confirm_existing'?'подтвердить связь с бронью':decision==='assign_slot'?'назначить новый слот без второй оплаты':decision==='refund_orphan'?'вернуть orphan-платёж без изменения дела':'направить платёж на возврат'}
async function resolveReview(id,decision,button){const x=rowsById.get(Number(id)),candidate=selectedCandidate(x),orphan=decision==='refund_orphan';if(!candidate&&!orphan){feedback('Сначала выберите консультацию после сверки истории','bad');return}let slotId=null;if(decision==='assign_slot'){slotId=Number(document.getElementById('slot_'+id)?.value||0);if(!slotId){feedback('Выберите свободный слот','bad');return}}const question=decision==='refund_pending'||orphan?'Укажите основание возврата:':'Укажите результат сверки:';const comment=prompt(question);if(!comment)return;if(comment.trim().length<5){feedback('Комментарий должен содержать не менее 5 символов','bad');return}let warning=`Подтвердите: ${decisionLabel(decision)} по платежу #${id}.`;if(decision==='refund_pending'&&candidate?.existing_booking_valid)warning+=' Подтверждённая консультация останется без изменений.';if(orphan)warning+=' Дело, текущая консультация и слот останутся без изменений.';if(!confirm(warning+' Действие будет записано в историю.'))return;return withPaymentAction(id,button,async()=>{try{const result=await api('/admin/payment-reviews/'+id+'/resolve',{method:'POST',body:JSON.stringify({decision,slot_id:slotId,consultation_id:candidate?candidate.id:null,comment})});terminalCaseId=Number(result.case_id)||terminalCaseId;feedback(`Решение по платежу #${result.payment_id} сохранено. Новый статус оплаты: ${result.payment_status}`,'ok');try{await load()}catch(e){feedback(`Решение сохранено, но экран не обновился: ${e.message}`,'warn')}}catch(e){feedback(`Решение не сохранено: ${e.message}`,'bad')}})}
boot();
</script>
</body>
</html>
"""