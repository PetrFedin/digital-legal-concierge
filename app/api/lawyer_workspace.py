from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.m1_claim_service import M1ClaimService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.lawyer.lawyer_decisions import LawyerDecisionService
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.message import Message
from app.models.payment import Payment
from app.models.user import User
from app.security.lawyer_access import require_lawyer_actor
from app.system.settings_service import SettingsService

router = APIRouter(prefix="/lawyer/workspace", tags=["lawyer-workspace"])
CLOSED_CASE_STATUSES = {
    CaseStatus.M1_REJECTED,
    CaseStatus.M1_CLOSED,
    CaseStatus.M2_CLOSED,
    CaseStatus.ARCHIVED,
}
CLIENT_WAIT_STATUSES = {
    CaseStatus.M1_DOCUMENTS_PENDING,
    CaseStatus.M1_DOCS_REQUESTED,
    CaseStatus.M1_WAITING_PAYMENT_30000,
    CaseStatus.M1_POWER_OF_ATTORNEY,
    CaseStatus.M1_WAITING_PAYMENT_70000,
    CaseStatus.M1_WAITING_SUCCESS_FEE,
    CaseStatus.M2_DESCRIPTION_PENDING,
    CaseStatus.M2_DOCUMENTS_OPTIONAL,
    CaseStatus.M2_SLOT_PENDING,
    CaseStatus.M2_PAYMENT_PENDING,
}
SLA_LABELS = {
    "NOT_STARTED": "SLA не запущен",
    "FIRST_RESPONSE_PENDING": "Ожидается первая реакция",
    "FIRST_RESPONSE_OK": "Первая реакция в срок",
    "FIRST_RESPONSE_OVERDUE": "Просрочена первая реакция",
    "ACTION_PENDING": "Ожидается действие",
    "ACTION_OK": "Действие выполнено в срок",
    "ACTION_OVERDUE": "Действие просрочено",
}
PAYMENT_LABELS = {
    PaymentStatus.PENDING: "ожидает открытия клиентом",
    PaymentStatus.WAITING_CONFIRMATION: "ожидает подтверждения провайдера",
    PaymentStatus.PAID: "оплачен",
    PaymentStatus.FAILED: "ошибка оплаты",
    PaymentStatus.CANCELLED: "отменён",
    PaymentStatus.EXPIRED: "истёк",
}


def _case_status(value: object) -> CaseStatus | None:
    try:
        return CaseStatus(str(value))
    except (TypeError, ValueError):
        return None


def _document_status(value: object) -> DocumentStatus | None:
    try:
        return DocumentStatus(str(value))
    except (TypeError, ValueError):
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _route_label(route: str | None) -> str:
    return {"M1": "Ведение дела", "M2": "Консультация"}.get(
        str(route or ""), "Юридическое обращение"
    )


def _sla_label(value: str | None) -> str:
    return SLA_LABELS.get(str(value or ""), "SLA не определён")


def _payment_label(value: str | None) -> str:
    return PAYMENT_LABELS.get(str(value or ""), str(value or "не определён"))


def _readiness_reason(error: ValueError) -> str:
    text = str(error).strip()
    prefix = "Нельзя принять дело: "
    return text[len(prefix) :] if text.startswith(prefix) else text


def _priority(
    *,
    is_overdue: bool,
    unread_client_messages: int,
    documents_on_review: int,
    consultation_today: bool,
    can_accept: bool,
    m1_action: str | None,
    fallback: str | None,
) -> tuple[str, str, str | None]:
    if is_overdue:
        return (
            "critical",
            "Устранить SLA-просрочку",
            "Свяжитесь с клиентом и зафиксируйте ближайшее действие.",
        )
    if unread_client_messages:
        return (
            "high",
            "Ответить клиенту",
            f"Непрочитанных сообщений: {unread_client_messages}",
        )
    if documents_on_review:
        return (
            "high",
            "Проверить документы",
            f"Ожидают решения: {documents_on_review}",
        )
    if consultation_today:
        return (
            "high",
            "Открыть консультацию",
            "По делу есть консультация на сегодня.",
        )
    if can_accept:
        return (
            "high",
            "Принять M1 и открыть договор",
            "Все актуальные документы приняты.",
        )
    if m1_action == "start_claim":
        return "high", "Начать подготовку претензии", "Доверенность подтверждена."
    if m1_action == "mark_claim_sent":
        return (
            "high",
            "Зафиксировать отправку претензии",
            "После подтверждения начнётся audited 30-дневный срок.",
        )
    if m1_action == "open_court":
        return "high", "Открыть судебный этап", "Audited 30-дневный срок истёк."
    if m1_action == "open_court_payment":
        return (
            "high",
            "Открыть второй платёж 70 000 ₽",
            "Зафиксируйте судебное событие или решение, после которого наступает второй договорный платёж.",
        )
    if m1_action == "record_money_received":
        return (
            "high",
            "Зафиксировать фактически взысканную сумму",
            "Укажите только реально полученную клиентом сумму. Система рассчитает success fee по действующей ставке.",
        )
    if m1_action == "wait_success_fee":
        return (
            "normal",
            "Ожидать финальный платёж клиента",
            "Success fee уже открыт. После подтверждения оплаты дело закроется автоматически.",
        )
    if m1_action == "wait_claim_period":
        return (
            "normal",
            "Ожидать истечения 30-дневного срока",
            "Судебный этап пока недоступен.",
        )
    return "normal", fallback or "Проверить текущее состояние дела", None


@router.get("/data")
async def workspace_data(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    lawyer_id = actor.lawyer.id
    now = datetime.now(timezone.utc)
    success_fee_percent = str(
        await SettingsService(db).get_value("payments.m1_success_fee_percent")
    )
    case_rows = (
        await db.execute(
            select(Case, User)
            .join(User, User.id == Case.client_id)
            .where(Case.assigned_lawyer_id == lawyer_id)
            .where(Case.status.notin_(CLOSED_CASE_STATUSES))
            .order_by(Case.updated_at.desc(), Case.id.desc())
            .limit(300)
        )
    ).all()
    case_ids = [case.id for case, _ in case_rows]
    docs_by_case: dict[int, list[Document]] = defaultdict(list)
    unread_by_case: dict[int, int] = defaultdict(int)
    consultations_by_case: dict[int, list[datetime]] = defaultdict(list)
    success_fee_payment_by_case: dict[int, Payment] = {}

    if case_ids:
        for item in (
            await db.execute(
                select(Document)
                .where(Document.case_id.in_(case_ids))
                .where(Document.status != DocumentStatus.ARCHIVED)
            )
        ).scalars().all():
            docs_by_case[item.case_id].append(item)
        for item in (
            await db.execute(
                select(Message)
                .where(Message.case_id.in_(case_ids))
                .where(Message.sender_type == "client")
                .where(Message.is_read.is_(False))
            )
        ).scalars().all():
            unread_by_case[item.case_id] += 1
        for item in (
            await db.execute(
                select(Consultation)
                .where(Consultation.case_id.in_(case_ids))
                .where(Consultation.lawyer_id == lawyer_id)
                .where(Consultation.status == ConsultationStatus.BOOKED)
                .where(Consultation.scheduled_at.is_not(None))
            )
        ).scalars().all():
            if item.scheduled_at:
                consultations_by_case[item.case_id].append(item.scheduled_at)
        for item in (
            await db.execute(
                select(Payment)
                .where(Payment.case_id.in_(case_ids))
                .where(Payment.payment_code == PaymentCode.M1_SUCCESS_FEE)
                .order_by(Payment.created_at.desc(), Payment.id.desc())
            )
        ).scalars().all():
            success_fee_payment_by_case.setdefault(item.case_id, item)

    claims = M1ClaimService(db)
    decisions = LawyerDecisionService(db)
    cases: list[dict[str, object]] = []
    summary = {
        "active_cases": len(case_rows),
        "requires_action": 0,
        "unread_client_messages": 0,
        "documents_on_review": 0,
        "overdue": 0,
        "waiting": 0,
        "consultations_today": 0,
    }

    for case, user in case_rows:
        status = _case_status(case.status)
        documents = docs_by_case.get(case.id, [])
        documents_on_review = sum(
            _document_status(item.status) == DocumentStatus.ON_REVIEW
            for item in documents
        )
        unread = int(unread_by_case.get(case.id, 0))
        today_times = [
            value
            for value in consultations_by_case.get(case.id, [])
            if _as_utc(value).date() == now.date()
        ]

        documents_ready = False
        readiness_reason: str | None = None
        if status == CaseStatus.M1_LAWYER_REVIEW:
            try:
                await decisions.assert_documents_ready_for_acceptance(case=case)
            except ValueError as error:
                readiness_reason = _readiness_reason(error)
            else:
                documents_ready = True

        can_accept = status == CaseStatus.M1_LAWYER_REVIEW and documents_ready
        can_request_documents = status in {
            CaseStatus.M1_DOCUMENTS_RECEIVED,
            CaseStatus.M1_LAWYER_REVIEW,
        }
        can_transfer_to_m2 = status in {
            CaseStatus.M1_DOCUMENTS_PENDING,
            CaseStatus.M1_DOCUMENTS_RECEIVED,
            CaseStatus.M1_LAWYER_REVIEW,
            CaseStatus.M1_DOCS_REQUESTED,
        }
        m1_action: str | None = None
        claim_due_at: str | None = None
        claim_remaining_seconds = 0
        claim_note: str | None = None
        if status == CaseStatus.M1_POA_RECEIVED:
            m1_action = "start_claim"
        elif status == CaseStatus.M1_CLAIM_PREPARATION:
            m1_action = "mark_claim_sent"
        elif status == CaseStatus.M1_WAITING_30_DAYS:
            eligibility = await claims.court_eligibility(case=case, now=now)
            claim_due_at = eligibility.due_at.isoformat() if eligibility.due_at else None
            claim_remaining_seconds = int(eligibility.remaining_seconds or 0)
            claim_note = eligibility.reason
            m1_action = "open_court" if eligibility.eligible else "wait_claim_period"
        elif status == CaseStatus.M1_COURT_STAGE:
            m1_action = "open_court_payment"
        elif status == CaseStatus.M1_ENFORCEMENT:
            m1_action = "record_money_received"
        elif status == CaseStatus.M1_WAITING_SUCCESS_FEE:
            m1_action = "wait_success_fee"

        fee_payment = success_fee_payment_by_case.get(case.id)
        overdue = str(case.sla_status or "").endswith("OVERDUE")
        priority, recommended_action, action_note = _priority(
            is_overdue=overdue,
            unread_client_messages=unread,
            documents_on_review=documents_on_review,
            consultation_today=bool(today_times),
            can_accept=bool(can_accept),
            m1_action=m1_action,
            fallback=case.next_action,
        )
        if m1_action == "wait_success_fee":
            if fee_payment:
                action_note = (
                    f"Финальный платёж {fee_payment.amount} ₽: "
                    f"{_payment_label(fee_payment.status)}. "
                    "Webhook закроет дело только после подтверждения оплаты."
                )
            else:
                priority = "critical"
                recommended_action = "Проверить финальный платёж"
                action_note = (
                    "Дело ожидает success fee, но платёж не найден. "
                    "Не меняйте статус вручную: требуется проверка финансового контура."
                )
        if priority in {"critical", "high"}:
            summary["requires_action"] += 1
        summary["unread_client_messages"] += unread
        summary["documents_on_review"] += int(documents_on_review)
        summary["overdue"] += int(overdue)
        summary["waiting"] += int(
            m1_action in {"wait_claim_period", "wait_success_fee"}
            or status in CLIENT_WAIT_STATUSES
        )
        summary["consultations_today"] += len(today_times)

        cases.append(
            {
                "case_id": case.id,
                "case_number": case.case_number,
                "client_name": user.full_name,
                "route": case.route,
                "route_label": _route_label(case.route),
                "status": case.status,
                "status_label": get_client_visible_status(case.status),
                "updated_at": case.updated_at.isoformat(),
                "sla_status": case.sla_status,
                "sla_label": _sla_label(case.sla_status),
                "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
                "unread_client_messages": unread,
                "documents_on_review": int(documents_on_review),
                "documents_ready": documents_ready,
                "readiness_reason": readiness_reason,
                "consultation_today": bool(today_times),
                "consultation_today_at": (
                    min(today_times).isoformat() if today_times else None
                ),
                "priority": priority,
                "recommended_action": recommended_action,
                "action_note": action_note,
                "can_accept": bool(can_accept),
                "can_request_documents": can_request_documents,
                "can_transfer_to_m2": can_transfer_to_m2,
                "m1_action": m1_action,
                "claim_due_at": claim_due_at,
                "claim_remaining_seconds": claim_remaining_seconds,
                "claim_note": claim_note,
                "success_fee_payment": (
                    {
                        "payment_id": fee_payment.id,
                        "amount": str(fee_payment.amount),
                        "status": fee_payment.status,
                        "status_label": _payment_label(fee_payment.status),
                    }
                    if fee_payment
                    else None
                ),
            }
        )

    return {
        "generated_at": now.isoformat(),
        "lawyer": {"id": lawyer_id, "name": actor.lawyer.full_name},
        "success_fee_percent": success_fee_percent,
        "summary": summary,
        "cases": cases,
    }


@router.get("/ui", response_class=HTMLResponse)
async def lawyer_workspace_ui():
    return HTMLResponse(WORKSPACE_HTML)


WORKSPACE_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Рабочий кабинет юриста</title>
<style>
:root{--bg:#f4f6fa;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--primary:#3157d5;--green:#14804a;--red:#b42318;--amber:#a15c00;--shadow:0 12px 34px rgba(16,24,40,.07)}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{position:sticky;top:0;z-index:20;background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px}header .inner,main{max-width:1240px;margin:auto}header .inner,.links,.tabs,.actions,.toolbar,.welcome{display:flex;gap:9px;align-items:center;flex-wrap:wrap}header .inner,.welcome,.toolbar{justify-content:space-between}h1{font-size:23px;margin:0 0 4px}header p{margin:0;color:#d0d5dd;font-size:13px}.button,button{border:0;border-radius:10px;padding:9px 12px;background:var(--primary);color:#fff;font-weight:750;cursor:pointer;text-decoration:none}.button:focus-visible,button:focus-visible,.search:focus-visible{outline:3px solid #c7d2fe;outline-offset:2px}.secondary{background:#475467}.green{background:var(--green)}.amber{background:var(--amber)}.red{background:var(--red)}main{padding:22px}.muted{color:var(--muted);font-size:13px}.metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}.metric,.card{background:#fff;border:1px solid var(--line);border-radius:15px;padding:14px;box-shadow:var(--shadow)}.metric b{display:block;font-size:24px}.metric span{font-size:12px;color:var(--muted)}.toolbar{margin:18px 0 12px}.tabs button{background:#fff;color:var(--ink);border:1px solid var(--line)}.tabs button.active{background:var(--primary);color:#fff}.search{width:min(430px,100%);padding:10px 12px;border:1px solid #d0d5dd;border-radius:10px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.card.critical{border-color:#fda29b}.card.high{border-color:#fedf89}.head{display:flex;justify-content:space-between;gap:12px}.head h3{margin:0 0 4px}.badge{border-radius:999px;padding:5px 9px;background:#eef2f6;font-size:12px;font-weight:750}.label{display:block;color:var(--muted);font-size:11px;font-weight:800;text-transform:uppercase;margin:12px 0 5px}.actionbox{background:#eef2ff;border:1px solid #c7d2fe;border-radius:12px;padding:12px}.deadline{background:#fff7e6;border:1px solid #fedf89;border-radius:11px;padding:10px;margin-top:10px}.meta{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.cell{background:#f8fafc;border-radius:10px;padding:9px}.cell span{display:block;color:var(--muted);font-size:11px}.form{display:none;border-top:1px solid var(--line);margin-top:12px;padding-top:12px}.form.open{display:block}.form textarea,.form input{width:100%;padding:10px;border:1px solid #d0d5dd;border-radius:10px;font:inherit}.form textarea{min-height:88px}.amount-wrap{display:none;margin-bottom:10px}.amount-preview{margin-top:8px;padding:10px;border-radius:10px;background:#ecfdf3;border:1px solid #abefc6;color:#067647;font-weight:700}.review-box,.form-error{display:none;border-radius:10px;padding:11px;margin-top:9px}.review-box{background:#f8fafc;border:1px solid var(--line)}.review-box.open,.form-error.open{display:block}.form-error{background:#fef3f2;color:var(--red);border:1px solid #fecdca}.message{min-height:22px;margin:10px 0}.ok{color:var(--green)}.bad{color:var(--red)}.warn-text{color:var(--amber)}.empty,.error{grid-column:1/-1;background:#fff;border:1px dashed var(--line);border-radius:15px;padding:30px;text-align:center;color:var(--muted)}@media(max-width:1000px){.metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:850px){.grid{grid-template-columns:1fr}}@media(max-width:620px){header .inner,.welcome,.toolbar{align-items:flex-start;flex-direction:column}.metrics{grid-template-columns:1fr 1fr}.meta{grid-template-columns:1fr}main{padding:14px}}
</style></head><body>
<header><div class="inner"><div><h1>⚖ Рабочий кабинет юриста</h1><p>Сообщения → документы → консультации → юридический этап → исполнение → закрытие</p></div><div class="links"><a class="button green" href="/lawyer/consultation-desk/ui">Консультации</a><a class="button secondary" href="/document-access/review/ui">Документы</a><a class="button secondary" href="/message-center/ui">Сообщения</a><a class="button secondary" href="/operator">Все разделы</a></div></div></header>
<main><div class="welcome"><div><h2 id="lawyerName">Рабочий день</h2><div id="freshness" class="muted"></div></div><button class="secondary" onclick="load(this)">Обновить</button></div><div id="metrics" class="metrics"></div><div class="toolbar"><div class="tabs"><button class="active" onclick="showTab('priority',this)">Требуют действий</button><button onclick="showTab('waiting',this)">Ожидают клиента/срок</button><button onclick="showTab('cases',this)">Все дела</button></div><input id="search" class="search" type="search" placeholder="Найти дело, клиента, статус или действие" oninput="render()"></div><div id="message" class="message"></div><div id="content" class="grid"></div></main>
<script>
let token='',data=null,currentTab='priority';const pending=new Set(),caseDrafts=new Map(),caseAmountDrafts=new Map();const content=document.getElementById('content'),message=document.getElementById('message'),metrics=document.getElementById('metrics'),search=document.getElementById('search'),lawyerName=document.getElementById('lawyerName'),freshness=document.getElementById('freshness');
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}function feedback(t,s=''){message.textContent=t;message.className='message '+s}function dt(v){return v?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—'}function duration(s){s=Math.max(0,Number(s||0));const d=Math.floor(s/86400),h=Math.floor((s%86400)/3600);return d?`${d} д. ${h} ч.`:h?`${h} ч.`:'менее часа'}function safeHref(v,f='/lawyer/workspace/ui'){const x=String(v||'');return x.startsWith('/')&&!x.startsWith('//')?x:f}function parseMoney(v){const n=Number(String(v||'').replace(/\s/g,'').replace(',','.'));return Number.isFinite(n)?n:NaN}function rub(v){return new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(v||0))+' ₽'}
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';const e=new Error('Сессия истекла или недостаточно прав');e.status=r.status;throw e}const d=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(d.detail||'Ошибка запроса');e.status=r.status;throw e}return d}
async function boot(){try{const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('lawyer'))throw new Error('Требуется роль юриста');token=s.api_token||'';await load()}catch(e){showError(e)}}function showError(e){content.innerHTML=`<div class="error"><b>Не удалось загрузить кабинет</b><p>${esc(e.message||e)}</p><button onclick="load()">Повторить</button></div>`;feedback(e.message||String(e),'bad')}
async function load(button=null,propagate=false){if(button)button.disabled=true;try{data=await api('/lawyer/workspace/data');lawyerName.textContent=data.lawyer?.name||'Рабочий день';freshness.textContent='Обновлено '+dt(data.generated_at);renderMetrics();render()}catch(e){if(propagate)throw e;showError(e)}finally{if(button)button.disabled=false}}function renderMetrics(){const s=data.summary||{};metrics.innerHTML=`<div class="metric"><b>${s.active_cases||0}</b><span>активных дел</span></div><div class="metric"><b>${s.requires_action||0}</b><span>требуют действия</span></div><div class="metric"><b>${s.unread_client_messages||0}</b><span>непрочитанных</span></div><div class="metric"><b>${s.documents_on_review||0}</b><span>документов на проверке</span></div><div class="metric"><b>${s.overdue||0}</b><span>SLA-просрочек</span></div><a class="metric" href="/lawyer/consultation-desk/ui"><b>${s.consultations_today||0}</b><span>консультаций сегодня →</span></a>`}function showTab(t,b){currentTab=t;document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('active',x===b));render()}
function render(){let rows=data?.cases||[];if(currentTab==='priority')rows=rows.filter(x=>['critical','high'].includes(x.priority));if(currentTab==='waiting')rows=rows.filter(x=>['wait_claim_period','wait_success_fee'].includes(x.m1_action)||['M1_DOCUMENTS_PENDING','M1_DOCS_REQUESTED','M1_WAITING_PAYMENT_30000','M1_POWER_OF_ATTORNEY','M1_WAITING_PAYMENT_70000','M1_WAITING_SUCCESS_FEE','M2_DESCRIPTION_PENDING','M2_DOCUMENTS_OPTIONAL','M2_SLOT_PENDING','M2_PAYMENT_PENDING'].includes(String(x.status||'')));const q=String(search.value||'').trim().toLowerCase();if(q)rows=rows.filter(x=>[x.case_number,x.client_name,x.status_label,x.recommended_action,x.action_note].some(v=>String(v||'').toLowerCase().includes(q)));content.innerHTML=rows.length?rows.map(caseCard).join(''):empty(q?'Ничего не найдено':currentTab==='priority'?'Срочных действий нет':'Активных дел нет',q?'Очистите поиск или откройте все дела.':'Очередь обработана.')}function empty(t,d){return `<div class="empty"><b>${esc(t)}</b><p>${esc(d)}</p>${search.value?'<button onclick="search.value=\'\';render()">Очистить поиск</button>':''}<button class="secondary" onclick="load()">Обновить</button></div>`}
function caseSnapshot(id){return (data?.cases||[]).find(x=>x.case_id===id)}function draftKey(id,type){return `${id}:${type}`}function caseControls(id){return Array.from(document.querySelectorAll(`[data-case-id="${id}"]`))}function primaryButton(x){if(String(x.sla_status||'').includes('OVERDUE')||x.unread_client_messages)return `<a class="button" href="/message-center/ui?case_id=${x.case_id}">Открыть переписку</a>`;if(x.documents_on_review)return `<a class="button amber" href="/document-access/review/ui?case_id=${x.case_id}">Проверить документы (${x.documents_on_review})</a>`;if(x.consultation_today)return `<a class="button green" href="/lawyer/consultation-desk/ui">Открыть консультацию</a>`;if(x.can_accept)return `<a class="button green" href="/document-access/review/ui?case_id=${x.case_id}">Принять дело</a>`;if(['start_claim','mark_claim_sent','open_court','open_court_payment','record_money_received'].includes(x.m1_action))return `<button class="green" data-case-id="${x.case_id}" onclick="openCaseForm(${x.case_id},'${x.m1_action}')">${esc(x.recommended_action)}</button>`;if(x.m1_action==='wait_claim_period')return `<button class="secondary" onclick="load(this)">Проверить срок</button>`;if(x.m1_action==='wait_success_fee')return `<a class="button secondary" href="/message-center/ui?case_id=${x.case_id}">Связаться с клиентом</a>`;return `<a class="button secondary" href="/message-center/ui?case_id=${x.case_id}">Открыть контекст дела</a>`}function deadlineBlock(x){if(!x.claim_due_at)return '';return `<div class="deadline"><b>Контрольный срок: ${esc(dt(x.claim_due_at))}</b><div class="muted">${x.m1_action==='wait_claim_period'?`До доступности судебного этапа: ${esc(duration(x.claim_remaining_seconds))}`:'30-дневный срок истёк; сервер повторно проверит его перед переходом.'}</div></div>`}function feeBlock(x){const p=x.success_fee_payment;if(!p)return '';return `<div class="deadline"><b>Success fee: ${esc(rub(p.amount))}</b><div class="muted">Статус платежа: ${esc(p.status_label||p.status)}</div></div>`}
function caseCard(x){const secondary=[`<a class="button secondary" href="/message-center/ui?case_id=${x.case_id}">Переписка</a>`];if(x.documents_on_review)secondary.push(`<a class="button secondary" href="/document-access/review/ui?case_id=${x.case_id}">Документы</a>`);if(x.can_request_documents)secondary.push(`<button class="amber" data-case-id="${x.case_id}" onclick="openCaseForm(${x.case_id},'documents')">Запросить документы</button>`);if(x.can_transfer_to_m2)secondary.push(`<button class="red" data-case-id="${x.case_id}" onclick="openCaseForm(${x.case_id},'transfer')">Перевести в консультацию</button>`);return `<article class="card ${esc(x.priority)}" id="case_${x.case_id}"><div class="head"><div><span class="label">Сейчас</span><h3>${esc(x.case_number)}</h3><div>${esc(x.client_name)} · ${esc(x.route_label)}</div></div><span class="badge">${esc(x.status_label)}</span></div><span class="label">Главный следующий шаг</span><div class="actionbox"><b>${esc(x.recommended_action)}</b>${x.action_note?`<div class="muted">${esc(x.action_note)}</div>`:''}<div class="actions" style="margin-top:9px">${primaryButton(x)}</div></div>${deadlineBlock(x)}${feeBlock(x)}<span class="label">Контекст</span><div class="meta"><div class="cell"><span>SLA</span>${esc(x.sla_label)}</div><div class="cell"><span>SLA-срок</span>${esc(dt(x.sla_due_at))}</div><div class="cell"><span>Сообщения</span>${esc(x.unread_client_messages||0)} непрочит.</div><div class="cell"><span>Документы</span>${esc(x.documents_on_review||0)} на проверке</div><div class="cell"><span>Консультация сегодня</span>${x.consultation_today?esc(dt(x.consultation_today_at)):'нет'}</div><div class="cell"><span>Обновлено</span>${esc(dt(x.updated_at))}</div></div><div class="actions" style="margin-top:12px">${secondary.join('')}</div><div class="form" id="case_form_${x.case_id}" data-stage="edit"><div id="case_edit_${x.case_id}"><h4 id="case_form_title_${x.case_id}"></h4><div class="amount-wrap" id="case_amount_wrap_${x.case_id}"><label class="label" for="case_amount_${x.case_id}">Фактически получено клиентом, ₽</label><input id="case_amount_${x.case_id}" inputmode="decimal" autocomplete="off" placeholder="Например, 250000,00" oninput="rememberAmountDraft(${x.case_id},this);updateRecoveryPreview(${x.case_id})"><div class="amount-preview" id="case_amount_preview_${x.case_id}"></div><div class="muted">Используйте сумму фактического поступления, а не расчётную неустойку или сумму иска.</div></div><label class="label" id="case_comment_label_${x.case_id}" for="case_comment_${x.case_id}">Комментарий / основание</label><textarea id="case_comment_${x.case_id}" oninput="rememberCaseDraft(${x.case_id},this)"></textarea><div id="case_hint_${x.case_id}" class="muted"></div><div class="actions"><button data-case-id="${x.case_id}" onclick="reviewCaseForm(${x.case_id},this)">Проверить действие</button><button class="secondary" onclick="closeCaseForm(${x.case_id})">Закрыть</button></div></div><div class="review-box" id="case_review_${x.case_id}"><b id="case_review_title_${x.case_id}"></b><p id="case_review_effect_${x.case_id}"></p><div id="case_review_amount_wrap_${x.case_id}" style="display:none"><div class="muted">Фактически получено / предварительный success fee:</div><p id="case_review_amount_${x.case_id}"></p></div><div class="muted">Комментарий / основание:</div><p id="case_review_comment_${x.case_id}"></p><div class="actions"><button data-case-id="${x.case_id}" onclick="submitCaseForm(${x.case_id},this)">Подтвердить действие</button><button class="secondary" onclick="backToCaseEdit(${x.case_id})">← Изменить данные</button></div></div><div class="form-error" id="case_error_${x.case_id}"></div></div></article>`}
const configs={accept:{title:'Основание принятия дела',min:5,effect:'После подтверждения дело будет принято и перейдёт к этапу договора.'},documents:{title:'Какие документы запросить',min:5,effect:'После подтверждения клиент получит запрос дополнительных документов.'},transfer:{title:'Причина перевода в консультацию',min:10,effect:'После подтверждения дело перейдёт в консультационный маршрут.'},start_claim:{title:'Начать подготовку претензии',min:0,effect:'Откроется подготовка претензии; 30-дневный срок ещё не начнётся.'},mark_claim_sent:{title:'Зафиксировать отправку претензии',min:5,effect:'Будет зафиксирована отправка и начнётся audited 30-дневный срок.'},open_court:{title:'Открыть судебный этап',min:5,effect:'Сервер повторно проверит audited срок и только после этого откроет суд.'},open_court_payment:{title:'Открыть второй платёж 70 000 ₽',min:5,effect:'После подтверждения дело перейдёт в ожидание второго платежа, а клиент получит реальный следующий шаг оплаты 70 000 ₽.'},record_money_received:{title:'Зафиксировать фактическое взыскание',min:0,needsAmount:true,effect:'После подтверждения сервер сохранит фактически полученную сумму, рассчитает success fee по действующей ставке и откроет клиенту финальный платёж.'}};function typeAvailable(x,t){if(t==='documents')return !!x.can_request_documents;if(t==='transfer')return !!x.can_transfer_to_m2;if(t==='accept')return !!x.can_accept;return x.m1_action===t&&['start_claim','mark_claim_sent','open_court','open_court_payment','record_money_received'].includes(t)}
function rememberCaseDraft(id,textarea){const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'';if(type)caseDrafts.set(draftKey(id,type),textarea.value)}function rememberAmountDraft(id,input){const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'';if(type)caseAmountDrafts.set(draftKey(id,type),input.value)}function clearCaseError(id){const b=document.getElementById('case_error_'+id);if(b){b.textContent='';b.classList.remove('open')}}function showCaseError(id,t,refresh=false){const b=document.getElementById('case_error_'+id);if(!b)return;b.innerHTML=`<div>${esc(t)}</div>${refresh?`<button class="secondary" onclick="refreshCaseAfterConflict(${id},this)">Обновить карточку</button>`:''}`;b.classList.add('open')}function setCaseStage(id,s){const f=document.getElementById('case_form_'+id),e=document.getElementById('case_edit_'+id),r=document.getElementById('case_review_'+id);f.dataset.stage=s;e.style.display=s==='edit'?'block':'none';r.classList.toggle('open',s==='review')}
function updateRecoveryPreview(id){const form=document.getElementById('case_form_'+id);if(form?.dataset.type!=='record_money_received')return;const amount=parseMoney(document.getElementById('case_amount_'+id)?.value),percent=Number(data?.success_fee_percent||0),box=document.getElementById('case_amount_preview_'+id);if(!box)return;if(!(amount>0)||!(percent>0)){box.textContent=`Success fee: ${esc(percent)}% от фактически полученной суммы`;return}box.textContent=`Предварительно: ${rub(amount)} × ${percent}% = ${rub(amount*percent/100)}`}
function openCaseForm(id,type){document.querySelectorAll('.form').forEach(x=>x.classList.remove('open'));const x=caseSnapshot(id),c=configs[type];if(!x||!c||!typeAvailable(x,type)){feedback('Действие уже недоступно. Обновите карточку.','warn-text');return}const f=document.getElementById('case_form_'+id),ta=document.getElementById('case_comment_'+id),amountWrap=document.getElementById('case_amount_wrap_'+id),amountInput=document.getElementById('case_amount_'+id),commentLabel=document.getElementById('case_comment_label_'+id);f.dataset.type=type;document.getElementById('case_form_title_'+id).textContent=c.title;ta.value=caseDrafts.get(draftKey(id,type))||'';amountWrap.style.display=c.needsAmount?'block':'none';amountInput.value=c.needsAmount?(caseAmountDrafts.get(draftKey(id,type))||''):'';commentLabel.textContent=c.needsAmount?'Комментарий / подтверждающий ориентир (необязательно)':'Комментарий / основание';document.getElementById('case_hint_'+id).textContent=c.min?`Минимум ${c.min} символов. Черновик сохранится до успешной записи.`:'Комментарий необязателен. Черновик сохранится до успешной записи.';clearCaseError(id);setCaseStage(id,'edit');f.classList.add('open');if(c.needsAmount){updateRecoveryPreview(id);amountInput.focus()}else ta.focus()}function closeCaseForm(id){const form=document.getElementById('case_form_'+id),textarea=document.getElementById('case_comment_'+id),amount=document.getElementById('case_amount_'+id);if(form&&textarea&&form.dataset.type){caseDrafts.set(draftKey(id,form.dataset.type),textarea.value);if(configs[form.dataset.type]?.needsAmount)caseAmountDrafts.set(draftKey(id,form.dataset.type),amount?.value||'')}form?.classList.remove('open')}function validateCaseComment(id){const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'',c=configs[type],ta=document.getElementById('case_comment_'+id),comment=(ta?.value||'').trim();if(!c||comment.length<c.min){showCaseError(id,`Комментарий должен содержать не менее ${c?.min||0} символов.`);return null}let amount=null;if(c.needsAmount){const raw=(document.getElementById('case_amount_'+id)?.value||'').trim();amount=parseMoney(raw);if(!(amount>0)){showCaseError(id,'Укажите фактически полученную сумму больше 0 ₽.');return null}caseAmountDrafts.set(draftKey(id,type),raw)}caseDrafts.set(draftKey(id,type),comment);return {form,type,comment,c,amount}}
function reviewCaseForm(id){const v=validateCaseComment(id);if(!v)return;document.getElementById('case_review_title_'+id).textContent='Проверка: '+v.c.title;let effect=v.c.effect;const amountWrap=document.getElementById('case_review_amount_wrap_'+id),amountText=document.getElementById('case_review_amount_'+id);if(v.c.needsAmount){const percent=Number(data?.success_fee_percent||0),fee=v.amount*percent/100;amountWrap.style.display='block';amountText.textContent=`${rub(v.amount)} → ${percent}% = ${rub(fee)}. Итоговую сумму повторно рассчитает сервер.`}else{amountWrap.style.display='none';amountText.textContent=''}document.getElementById('case_review_effect_'+id).textContent=effect;document.getElementById('case_review_comment_'+id).textContent=v.comment||'Без комментария';setCaseStage(id,'review')}function backToCaseEdit(id){setCaseStage(id,'edit');const form=document.getElementById('case_form_'+id);if(configs[form?.dataset.type]?.needsAmount)document.getElementById('case_amount_'+id)?.focus();else document.getElementById('case_comment_'+id)?.focus()}
async function withAction(key,controls,button,work){if(pending.has(key))return;pending.add(key);controls.forEach(x=>x.disabled=true);try{return await work()}finally{pending.delete(key);controls.forEach(x=>x.disabled=false)}}async function refreshCaseAfterConflict(id,button){const form=document.getElementById('case_form_'+id),textarea=document.getElementById('case_comment_'+id),amount=document.getElementById('case_amount_'+id);if(form&&textarea&&form.dataset.type){caseDrafts.set(draftKey(id,form.dataset.type),textarea.value);if(configs[form.dataset.type]?.needsAmount)caseAmountDrafts.set(draftKey(id,form.dataset.type),amount?.value||'')}try{await load(button,true);feedback('Карточка обновлена. Черновик сохранён — проверьте действие ещё раз.','warn-text')}catch(e){feedback('Не удалось обновить карточку: '+e.message,'bad')}}
async function submitCaseForm(id,button){const x=caseSnapshot(id),form=document.getElementById('case_form_'+id),type=form?.dataset.type||'',comment=(document.getElementById('case_comment_'+id)?.value||'').trim(),c=configs[type];if(!x||!typeAvailable(x,type)){showCaseError(id,'Карточка дела изменилась. Черновик сохранён.',true);return}if(form.dataset.stage!=='review'){showCaseError(id,'Сначала проверьте действие перед сохранением.');return}if(!c||comment.length<c.min){showCaseError(id,'Комментарий не соответствует требованиям действия.');return}let amount=null,amountRaw='';if(c.needsAmount){amountRaw=(document.getElementById('case_amount_'+id)?.value||'').trim();amount=parseMoney(amountRaw);if(!(amount>0)){showCaseError(id,'Фактически полученная сумма должна быть больше 0 ₽.');backToCaseEdit(id);return}caseAmountDrafts.set(draftKey(id,type),amountRaw)}caseDrafts.set(draftKey(id,type),comment);const paths={accept:`/lawyer/cases/${id}/accept`,documents:`/lawyer/cases/${id}/request-documents`,transfer:`/lawyer/cases/${id}/transfer-to-m2`,start_claim:`/lawyer/cases/${id}/claim/start`,mark_claim_sent:`/lawyer/cases/${id}/claim/sent`,open_court:`/lawyer/cases/${id}/court/open`,open_court_payment:`/lawyer/cases/${id}/court/payment/open`,record_money_received:`/lawyer/cases/${id}/enforcement/money-received`};const body={comment,reason:type==='transfer'?comment:undefined,amount:c.needsAmount?amountRaw:undefined,expected_status:x.status,expected_updated_at:x.updated_at};return withAction(`case:${id}`,caseControls(id),button,async()=>{let result;try{result=await api(paths[type],{method:'POST',body:JSON.stringify(body)})}catch(e){if(e.status===409){caseDrafts.set(draftKey(id,type),comment);if(c.needsAmount)caseAmountDrafts.set(draftKey(id,type),amountRaw);showCaseError(id,'Карточка дела изменилась или финансовые данные требуют проверки. Черновик сохранён.',true);feedback('Операция не выполнена. Черновик сохранён, данные дела не изменены.','warn-text')}else{showCaseError(id,e.message);feedback('Операция не сохранена: '+e.message,'bad')}return}caseDrafts.delete(draftKey(id,type));caseAmountDrafts.delete(draftKey(id,type));try{await load(null,true)}catch(e){const t=type==='accept'?'Дело принято, но кабинет не обновился':type==='documents'?'Запрос сохранён, но кабинет не обновился':type==='record_money_received'?'Поступление сохранено и финальный платёж открыт, но кабинет не обновился':'Операция сохранена, но кабинет не обновился';feedback(t+': '+e.message,'warn-text');return}const success={accept:'Дело принято. Этап договора открыт.',documents:'Запрос документов отправлен клиенту.',transfer:'Дело переведено в консультационный маршрут.',start_claim:'Подготовка претензии начата.',mark_claim_sent:'Отправка претензии зафиксирована. 30-дневный срок запущен.',open_court:'Судебный этап открыт.',open_court_payment:'Второй платёж открыт. Клиент получил следующий шаг оплаты.',record_money_received:`Фактическое взыскание сохранено. Success fee ${result?.success_fee_amount?rub(result.success_fee_amount):'рассчитан'} и открыт клиенту.`};feedback(success[type]||'Операция сохранена.','ok')})}
boot();
</script></body></html>
"""
