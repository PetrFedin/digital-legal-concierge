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
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.lawyer.lawyer_decisions import LawyerDecisionService
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.message import Message
from app.models.user import User
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(prefix="/lawyer/workspace", tags=["lawyer-workspace"])

CLOSED_CASE_STATUSES = {
    CaseStatus.M1_REJECTED,
    CaseStatus.M1_CLOSED,
    CaseStatus.M2_CLOSED,
    CaseStatus.ARCHIVED,
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


def _consultations_today_count(values: list[datetime | None], *, now: datetime) -> int:
    today = _as_utc(now).date()
    return sum(
        1
        for value in values
        if value is not None and _as_utc(value).date() == today
    )


def _remove_readiness_prefix(error: ValueError) -> str:
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
            "Откройте переписку, свяжитесь с клиентом и зафиксируйте действие.",
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
            "Все актуальные документы приняты; принятие выполняется отдельным подтверждением.",
        )
    if m1_action == "start_claim":
        return (
            "high",
            "Начать подготовку претензии",
            "Доверенность подтверждена; следующий юридический этап готов к запуску.",
        )
    if m1_action == "mark_claim_sent":
        return (
            "high",
            "Зафиксировать отправку претензии",
            "После подтверждения начнётся юридически значимый 30-дневный срок.",
        )
    if m1_action == "open_court":
        return (
            "high",
            "Открыть судебный этап",
            "Audited 30-дневный срок истёк; переход требует отдельного подтверждения.",
        )
    if m1_action == "wait_claim_period":
        return (
            "normal",
            "Ожидать истечения 30-дневного срока",
            "Судебный этап пока недоступен; кабинет покажет действие после истечения срока.",
        )
    return ("normal", fallback or "Проверить текущее состояние дела", None)


@router.get("/data")
async def workspace_data(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    lawyer_id = actor.lawyer.id
    now = datetime.now(timezone.utc)

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

    documents_by_case: dict[int, list[Document]] = defaultdict(list)
    unread_by_case: dict[int, int] = defaultdict(int)
    consultation_times_by_case: dict[int, list[datetime]] = defaultdict(list)

    if case_ids:
        documents = (
            await db.execute(
                select(Document)
                .where(Document.case_id.in_(case_ids))
                .where(Document.status != DocumentStatus.ARCHIVED)
                .order_by(Document.created_at.desc(), Document.id.desc())
            )
        ).scalars().all()
        for document in documents:
            documents_by_case[document.case_id].append(document)

        unread_messages = (
            await db.execute(
                select(Message)
                .where(Message.case_id.in_(case_ids))
                .where(Message.sender_type == "client")
                .where(Message.is_read.is_(False))
            )
        ).scalars().all()
        for item in unread_messages:
            unread_by_case[item.case_id] += 1

        consultations = (
            await db.execute(
                select(Consultation)
                .where(Consultation.case_id.in_(case_ids))
                .where(Consultation.lawyer_id == lawyer_id)
                .where(Consultation.status == ConsultationStatus.BOOKED)
                .where(Consultation.scheduled_at.is_not(None))
            )
        ).scalars().all()
        for consultation in consultations:
            if consultation.scheduled_at:
                consultation_times_by_case[consultation.case_id].append(
                    consultation.scheduled_at
                )

    claim_service = M1ClaimService(db)
    decision_service = LawyerDecisionService(db)
    cases: list[dict[str, object]] = []
    overdue_count = 0
    review_count = 0
    unread_count = 0
    actionable_count = 0
    waiting_count = 0

    for case, user in case_rows:
        status = _case_status(case.status)
        documents = documents_by_case.get(case.id, [])
        documents_on_review = sum(
            _document_status(document.status) == DocumentStatus.ON_REVIEW
            for document in documents
        )
        unread = int(unread_by_case.get(case.id, 0))
        today_times = [
            value
            for value in consultation_times_by_case.get(case.id, [])
            if _as_utc(value).date() == now.date()
        ]
        consultation_today = bool(today_times)

        documents_ready = False
        readiness_reason: str | None = None
        if status == CaseStatus.M1_LAWYER_REVIEW:
            try:
                await decision_service.assert_documents_ready_for_acceptance(case=case)
            except ValueError as error:
                readiness_reason = _remove_readiness_prefix(error)
            else:
                documents_ready = True

        can_accept = bool(status == CaseStatus.M1_LAWYER_REVIEW and documents_ready)
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
            eligibility = await claim_service.court_eligibility(case=case, now=now)
            claim_due_at = (
                eligibility.due_at.isoformat() if eligibility.due_at else None
            )
            claim_remaining_seconds = int(eligibility.remaining_seconds or 0)
            claim_note = eligibility.reason
            m1_action = "open_court" if eligibility.eligible else "wait_claim_period"

        is_overdue = str(case.sla_status or "").endswith("OVERDUE")
        priority, recommended_action, action_note = _priority(
            is_overdue=is_overdue,
            unread_client_messages=unread,
            documents_on_review=documents_on_review,
            consultation_today=consultation_today,
            can_accept=can_accept,
            m1_action=m1_action,
            fallback=case.next_action,
        )
        if priority in {"critical", "high"}:
            actionable_count += 1
        if is_overdue:
            overdue_count += 1
        if m1_action == "wait_claim_period" or status in CLIENT_WAIT_STATUSES:
            waiting_count += 1
        review_count += int(documents_on_review)
        unread_count += unread

        cases.append(
            {
                "case_id": case.id,
                "case_number": case.case_number,
                "client_name": user.full_name,
                "telegram_id": user.telegram_id,
                "route": case.route,
                "route_label": _route_label(case.route),
                "status": case.status,
                "status_label": get_client_visible_status(case.status),
                "updated_at": case.updated_at.isoformat(),
                "sla_status": case.sla_status,
                "sla_label": _sla_label(case.sla_status),
                "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
                "escalation_level": int(case.escalation_level or 0),
                "unread_client_messages": unread,
                "documents_on_review": int(documents_on_review),
                "documents_ready": documents_ready,
                "readiness_reason": readiness_reason,
                "consultation_today": consultation_today,
                "consultation_today_at": (
                    min(today_times).isoformat() if today_times else None
                ),
                "priority": priority,
                "recommended_action": recommended_action,
                "action_note": action_note,
                "can_accept": can_accept,
                "can_request_documents": can_request_documents,
                "can_transfer_to_m2": can_transfer_to_m2,
                "m1_action": m1_action,
                "claim_due_at": claim_due_at,
                "claim_remaining_seconds": claim_remaining_seconds,
                "claim_note": claim_note,
            }
        )

    booked_values = [
        value
        for values in consultation_times_by_case.values()
        for value in values
    ]
    return {
        "generated_at": now.isoformat(),
        "lawyer": {"id": lawyer_id, "name": actor.lawyer.full_name},
        "summary": {
            "active_cases": len(cases),
            "requires_action": actionable_count,
            "unread_client_messages": unread_count,
            "documents_on_review": review_count,
            "overdue": overdue_count,
            "waiting": waiting_count,
            "consultations_today": _consultations_today_count(booked_values, now=now),
        },
        "cases": cases,
    }


@router.get("/ui", response_class=HTMLResponse)
async def lawyer_workspace_ui():
    return HTMLResponse(WORKSPACE_HTML)


WORKSPACE_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Рабочий кабинет юриста — Digital Legal Concierge</title>
<style>
:root{--bg:#f4f6fa;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--primary:#3157d5;--primary-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fef3f2;--amber:#a15c00;--amber-soft:#fff7e6;--shadow:0 12px 34px rgba(16,24,40,.07)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{position:sticky;top:0;z-index:20;background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px;box-shadow:0 4px 18px rgba(16,24,40,.14)}header .inner{max-width:1240px;margin:auto;display:flex;justify-content:space-between;gap:18px;align-items:center}h1{font-size:23px;margin:0 0 4px}header p{margin:0;color:#d0d5dd;font-size:13px}.links,.row,.tabs,.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.button,button{border:0;border-radius:10px;padding:9px 12px;background:var(--primary);color:#fff;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}.secondary{background:#475467}.green{background:var(--green)}.amber{background:var(--amber)}.red{background:var(--red)}button:disabled,textarea:disabled,input:disabled{opacity:.55;cursor:wait}main{max-width:1240px;margin:auto;padding:22px}.welcome,.toolbar{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:14px}.welcome h2{margin:0 0 4px}.muted{color:var(--muted);font-size:13px}.metric-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}.metric{background:#fff;border:1px solid var(--line);border-radius:15px;padding:13px;box-shadow:var(--shadow);color:var(--ink);text-decoration:none}.metric b{display:block;font-size:25px;margin-bottom:3px}.metric span{color:var(--muted);font-size:12px}.metric.urgent{background:var(--red-soft);border-color:#fecdca}.metric.warn{background:var(--amber-soft);border-color:#fedf89}.metric.consult{background:var(--green-soft);border-color:#abefc6}.toolbar{margin:18px 0 12px;align-items:center}.tabs button{background:#fff;color:var(--ink);border:1px solid var(--line)}.tabs button.active{background:var(--primary);color:#fff;border-color:var(--primary)}.search{min-width:300px;max-width:430px;width:100%;border:1px solid #d0d5dd;border-radius:10px;padding:10px 12px;background:#fff}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.card{background:var(--surface);border:1px solid var(--line);border-radius:17px;padding:16px;box-shadow:var(--shadow)}.card.critical{border-color:#fda29b;background:#fffafa}.card.high{border-color:#fedf89}.card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.card h3{margin:0 0 4px;font-size:17px}.badge{display:inline-flex;border-radius:999px;padding:5px 9px;background:#eef2f6;font-size:12px;font-weight:750}.badge.red{background:var(--red-soft);color:var(--red)}.badge.amber{background:var(--amber-soft);color:var(--amber)}.section-label{display:block;color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;margin:12px 0 5px}.action-box{background:var(--primary-soft);border:1px solid #c7d2fe;border-radius:12px;padding:12px;margin:5px 0 12px}.action-box b{display:block;margin-bottom:4px}.deadline{background:var(--amber-soft);border:1px solid #fedf89;border-radius:11px;padding:10px;margin:10px 0}.meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.cell{background:#f8fafc;border-radius:10px;padding:9px}.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.form{display:none;border-top:1px solid var(--line);margin-top:12px;padding-top:12px}.form.open{display:block}.form textarea{width:100%;border:1px solid #d0d5dd;border-radius:10px;padding:9px;margin:5px 0;min-height:86px;resize:vertical}.review-box{display:none;background:#f8fafc;border:1px solid var(--line);border-radius:12px;padding:12px;margin-top:8px}.review-box.open{display:block}.review-box p{margin:6px 0 10px;white-space:pre-wrap;word-break:break-word}.form-error{display:none;background:var(--red-soft);border:1px solid #fecdca;border-radius:10px;color:var(--red);padding:10px;margin:9px 0}.form-error.open{display:block}.message{min-height:22px;margin:10px 0}.ok{color:var(--green)}.bad{color:var(--red)}.warn-text{color:var(--amber)}.empty,.error,.loading{grid-column:1/-1;padding:32px;text-align:center;border:1px dashed var(--line);border-radius:15px;background:#fff;color:var(--muted)}.error{background:var(--red-soft);color:var(--red)}
@media(max-width:1050px){.metric-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:900px){.grid{grid-template-columns:1fr}}@media(max-width:650px){header .inner,.welcome,.toolbar{align-items:flex-start;flex-direction:column}.metric-grid{grid-template-columns:1fr 1fr}main{padding:14px}.meta{grid-template-columns:1fr}.links{width:100%}.links .button{flex:1;text-align:center}.search{min-width:0}}
</style>
</head>
<body>
<header><div class="inner"><div><h1>⚖ Рабочий кабинет юриста</h1><p>Единая очередь: сообщения → документы → консультации → юридический этап дела</p></div><div class="links"><a class="button green" href="/lawyer/consultation-desk/ui">Консультации</a><a class="button secondary" href="/document-access/review/ui">Документы</a><a class="button secondary" href="/message-center/ui">Сообщения</a><a class="button secondary" href="/operator">Все разделы</a><form method="post" action="/logout" style="margin:0"><button class="secondary" type="submit">Выйти</button></form></div></div></header>
<main><div class="welcome"><div><h2 id="lawyerName">Рабочий день</h2><div id="freshness" class="muted"></div></div><button class="secondary" onclick="load(this)">Обновить</button></div><div id="metrics" class="metric-grid"></div><div class="toolbar"><div class="tabs"><button class="active" onclick="showTab('priority',this)">Требуют действий</button><button onclick="showTab('waiting',this)">Ожидают клиента/срок</button><button onclick="showTab('cases',this)">Все дела</button></div><input id="search" class="search" type="search" placeholder="Найти дело, клиента, статус или действие" oninput="render()"></div><div id="message" class="message" role="status" aria-live="polite"></div><div id="content" class="grid"><div class="loading">Загрузка кабинета…</div></div></main>
<script>
let token='',data=null,currentTab='priority';const pending=new Set(),caseDrafts=new Map();const content=document.getElementById('content'),message=document.getElementById('message'),metrics=document.getElementById('metrics'),freshness=document.getElementById('freshness'),lawyerName=document.getElementById('lawyerName'),search=document.getElementById('search');
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}
function feedback(text,state=''){message.textContent=text;message.className='message '+state}
function dt(v){return v?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—'}
function duration(seconds){let s=Math.max(0,Number(seconds||0));const d=Math.floor(s/86400),h=Math.floor((s%86400)/3600);return d?`${d} д. ${h} ч.`:h?`${h} ч.`:'менее часа'}
function safeHref(v,fallback='/lawyer/workspace/ui'){const x=String(v||'');return x.startsWith('/')&&!x.startsWith('//')?x:fallback}
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';const e=new Error('Сессия истекла или недостаточно прав');e.status=r.status;throw e}const d=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(d.detail||'Ошибка запроса');e.status=r.status;throw e}return d}
async function boot(){try{const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('lawyer'))throw new Error('Требуется роль юриста');token=s.api_token||'';await load()}catch(e){showError(e)}}
function showError(e){content.innerHTML=`<div class="error"><b>Не удалось загрузить кабинет</b><p>${esc(e.message||e)}</p><div class="actions" style="justify-content:center"><button onclick="load()">Повторить</button><a class="button secondary" href="/message-center/ui">Открыть сообщения</a></div></div>`;feedback(e.message||String(e),'bad')}
async function load(button=null,propagate=false){if(button){button.disabled=true;button.setAttribute('aria-busy','true')}if(!propagate)feedback('');try{data=await api('/lawyer/workspace/data');lawyerName.textContent=data.lawyer?.name||'Рабочий день';freshness.textContent='Обновлено '+dt(data.generated_at);renderMetrics();render()}catch(e){if(propagate)throw e;showError(e)}finally{if(button){button.disabled=false;button.removeAttribute('aria-busy')}}}
function renderMetrics(){const s=data.summary||{};metrics.innerHTML=`<div class="metric"><b>${s.active_cases||0}</b><span>активных дел</span></div><div class="metric warn"><b>${s.requires_action||0}</b><span>требуют действия</span></div><div class="metric warn"><b>${s.unread_client_messages||0}</b><span>непрочитанных сообщений</span></div><div class="metric warn"><b>${s.documents_on_review||0}</b><span>документов на проверке</span></div><div class="metric ${s.overdue?'urgent':''}"><b>${s.overdue||0}</b><span>SLA-просрочек</span></div><a class="metric consult" href="/lawyer/consultation-desk/ui"><b>${s.consultations_today||0}</b><span>консультаций сегодня · открыть →</span></a>`}
function showTab(tab,button){currentTab=tab;document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('active',x===button));render()}
function render(){if(!data)return;let rows=data.cases||[];if(currentTab==='priority')rows=rows.filter(x=>['critical','high'].includes(x.priority));if(currentTab==='waiting')rows=rows.filter(x=>x.m1_action==='wait_claim_period'||['M1_DOCUMENTS_PENDING','M1_DOCS_REQUESTED','M1_WAITING_PAYMENT_30000','M1_POWER_OF_ATTORNEY','M1_WAITING_PAYMENT_70000','M1_WAITING_SUCCESS_FEE','M2_DESCRIPTION_PENDING','M2_DOCUMENTS_OPTIONAL','M2_SLOT_PENDING','M2_PAYMENT_PENDING'].includes(String(x.status||'')));const q=String(search.value||'').trim().toLowerCase();if(q)rows=rows.filter(x=>[x.case_number,x.client_name,x.status_label,x.recommended_action,x.action_note].some(v=>String(v||'').toLowerCase().includes(q)));content.innerHTML=rows.length?rows.map(caseCard).join(''):empty(q?'Ничего не найдено':currentTab==='priority'?'Срочных действий нет':currentTab==='waiting'?'Нет дел в ожидании':'Активных дел нет',q?'Очистите поиск или откройте все дела.':'Очередь на текущий момент обработана.')}
function empty(title,detail){return `<div class="empty"><b>${esc(title)}</b><p>${esc(detail)}</p><div class="actions" style="justify-content:center">${search.value?'<button onclick="search.value=\'\';render()">Очистить поиск</button>':''}<button class="secondary" onclick="load()">Обновить</button></div></div>`}
function caseSnapshot(id){return (data?.cases||[]).find(x=>x.case_id===id)}
function caseControls(id){return Array.from(document.querySelectorAll(`[data-case-id="${id}"]`))}
function draftKey(id,type){return `${id}:${type}`}
function primaryButton(x){if(String(x.sla_status||'').includes('OVERDUE')||x.unread_client_messages)return `<a class="button" href="/message-center/ui?case_id=${x.case_id}">Открыть переписку</a>`;if(x.documents_on_review)return `<a class="button amber" href="/document-access/review/ui?case_id=${x.case_id}">Проверить документы (${x.documents_on_review})</a>`;if(x.consultation_today)return `<a class="button green" href="/lawyer/consultation-desk/ui">Открыть консультацию</a>`;if(x.can_accept)return `<a class="button green" href="/document-access/review/ui?case_id=${x.case_id}">Принять дело</a>`;if(['start_claim','mark_claim_sent','open_court'].includes(x.m1_action))return `<button class="${x.m1_action==='open_court'?'red':'green'}" data-case-id="${x.case_id}" onclick="openCaseForm(${x.case_id},'${x.m1_action}')">${esc(x.recommended_action)}</button>`;if(x.m1_action==='wait_claim_period')return `<button class="secondary" onclick="load(this)">Проверить срок</button>`;return `<a class="button secondary" href="/message-center/ui?case_id=${x.case_id}">Открыть контекст дела</a>`}
function deadlineBlock(x){if(!x.claim_due_at)return '';return `<div class="deadline"><b>Контрольный срок: ${esc(dt(x.claim_due_at))}</b><div class="muted">${x.m1_action==='wait_claim_period'?`До доступности судебного этапа: ${esc(duration(x.claim_remaining_seconds))}`:'30-дневный срок истёк. Судебный этап можно открыть после проверки основания.'}</div></div>`}
function caseCard(x){const overdue=String(x.sla_status||'').includes('OVERDUE'),secondary=[`<a class="button secondary" href="/message-center/ui?case_id=${x.case_id}">Переписка</a>`];if(x.documents_on_review)secondary.push(`<a class="button secondary" href="/document-access/review/ui?case_id=${x.case_id}">Документы</a>`);if(x.can_request_documents)secondary.push(`<button class="amber" data-case-id="${x.case_id}" onclick="openCaseForm(${x.case_id},'documents')">Запросить документы</button>`);if(x.can_transfer_to_m2)secondary.push(`<button class="red" data-case-id="${x.case_id}" onclick="openCaseForm(${x.case_id},'transfer')">Перевести в консультацию</button>`);return `<article class="card ${esc(x.priority)}" id="case_${x.case_id}"><div class="card-head"><div><span class="section-label">Сейчас</span><h3>${esc(x.case_number)}</h3><div>${esc(x.client_name)} · ${esc(x.route_label)}</div></div><span class="badge ${overdue?'red':x.priority==='high'?'amber':''}">${esc(x.status_label)}</span></div><span class="section-label">Главный следующий шаг</span><div class="action-box"><b>${esc(x.recommended_action)}</b>${x.action_note?`<div class="muted">${esc(x.action_note)}</div>`:''}<div class="actions" style="margin-top:10px">${primaryButton(x)}</div></div>${deadlineBlock(x)}<span class="section-label">Контекст</span><div class="meta"><div class="cell"><span>SLA</span>${esc(x.sla_label)}</div><div class="cell"><span>SLA-срок</span>${esc(dt(x.sla_due_at))}</div><div class="cell"><span>Сообщения клиента</span>${esc(x.unread_client_messages||0)} непрочит.</div><div class="cell"><span>Документы на проверке</span>${esc(x.documents_on_review||0)}</div><div class="cell"><span>Консультация сегодня</span>${x.consultation_today?esc(dt(x.consultation_today_at)):'нет'}</div><div class="cell"><span>Обновлено</span>${esc(dt(x.updated_at))}</div></div><div class="actions" style="margin-top:12px">${secondary.join('')}</div><div class="form" id="case_form_${x.case_id}" data-stage="edit"><div id="case_edit_${x.case_id}"><h4 id="case_form_title_${x.case_id}"></h4><textarea id="case_comment_${x.case_id}" oninput="rememberCaseDraft(${x.case_id},this)"></textarea><div id="case_hint_${x.case_id}" class="muted"></div><div class="actions"><button data-case-id="${x.case_id}" onclick="reviewCaseForm(${x.case_id},this)">Проверить действие</button><button class="secondary" onclick="closeCaseForm(${x.case_id})">Закрыть</button></div></div><div class="review-box" id="case_review_${x.case_id}"><b id="case_review_title_${x.case_id}"></b><p id="case_review_effect_${x.case_id}"></p><div class="muted">Комментарий / основание:</div><p id="case_review_comment_${x.case_id}"></p><div class="actions"><button data-case-id="${x.case_id}" onclick="submitCaseForm(${x.case_id},this)">Подтвердить действие</button><button class="secondary" onclick="backToCaseEdit(${x.case_id})">← Изменить комментарий</button><button class="secondary" onclick="closeCaseForm(${x.case_id})">Отмена</button></div></div><div class="form-error" id="case_error_${x.case_id}" role="alert"></div></div></article>`}
function typeAvailable(x,type){if(type==='documents')return Boolean(x.can_request_documents);if(type==='transfer')return Boolean(x.can_transfer_to_m2);if(type==='accept')return Boolean(x.can_accept);return x.m1_action===type&&['start_claim','mark_claim_sent','open_court'].includes(type)}
const actionConfigs={accept:{title:'Основание принятия дела',placeholder:'Комментарий клиенту',min:5,effect:'После подтверждения дело будет принято и перейдёт к этапу договора.'},documents:{title:'Какие документы запросить',placeholder:'Перечислите недостающие документы',min:5,effect:'После подтверждения клиент получит запрос дополнительных документов.'},transfer:{title:'Почему дело переводится в консультацию',placeholder:'Укажите причину перевода',min:10,effect:'После подтверждения дело перейдёт в консультационный маршрут.'},start_claim:{title:'Начать подготовку претензии',placeholder:'Комментарий клиенту (необязательно)',min:0,effect:'Дело перейдёт в подготовку претензии. Отправка претензии и 30-дневный срок пока не начнутся.'},mark_claim_sent:{title:'Зафиксировать отправку претензии',placeholder:'Способ отправки, трек-номер или подтверждение вручения',min:5,effect:'После подтверждения будет зафиксирована отправка и начнётся audited 30-дневный контрольный срок.'},open_court:{title:'Открыть судебный этап',placeholder:'Основание: срок истёк, результат претензии, дальнейшая стратегия',min:5,effect:'После подтверждения дело перейдёт в судебный этап. Сервер повторно проверит audited 30-дневный срок.'}};
function clearCaseError(id){const box=document.getElementById('case_error_'+id);if(box){box.textContent='';box.classList.remove('open')}}
function showCaseError(id,text,canRefresh=false){const box=document.getElementById('case_error_'+id);if(!box)return;box.innerHTML=`<div>${esc(text)}</div>${canRefresh?`<button class="secondary" type="button" onclick="refreshCaseAfterConflict(${id},this)">Обновить карточку</button>`:''}`;box.classList.add('open')}
function setCaseStage(id,stage){const form=document.getElementById('case_form_'+id),edit=document.getElementById('case_edit_'+id),review=document.getElementById('case_review_'+id);if(!form||!edit||!review)return;form.dataset.stage=stage;edit.style.display=stage==='edit'?'block':'none';review.classList.toggle('open',stage==='review')}
function rememberCaseDraft(id,textarea){const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'';if(type)caseDrafts.set(draftKey(id,type),textarea.value)}
function openCaseForm(id,type){document.querySelectorAll('.form').forEach(x=>x.classList.remove('open'));const x=caseSnapshot(id),config=actionConfigs[type];if(!x||!config){feedback('Карточка дела устарела. Обновите кабинет.','bad');return}if(!typeAvailable(x,type)){feedback('Это действие уже недоступно на текущем этапе. Обновите карточку дела.','warn-text');return}const form=document.getElementById('case_form_'+id),textarea=document.getElementById('case_comment_'+id);form.dataset.type=type;document.getElementById('case_form_title_'+id).textContent=config.title;textarea.placeholder=config.placeholder;textarea.value=caseDrafts.get(draftKey(id,type))||'';document.getElementById('case_hint_'+id).textContent=config.min?`Минимум ${config.min} символов. Черновик сохранится до успешной записи.`:'Комментарий необязателен. Черновик сохранится до успешной записи.';clearCaseError(id);setCaseStage(id,'edit');form.classList.add('open');textarea.focus()}
function closeCaseForm(id){const form=document.getElementById('case_form_'+id),textarea=document.getElementById('case_comment_'+id);if(form&&textarea&&form.dataset.type)caseDrafts.set(draftKey(id,form.dataset.type),textarea.value);form?.classList.remove('open')}
function validateCaseComment(id){const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'',config=actionConfigs[type],textarea=document.getElementById('case_comment_'+id),comment=(textarea?.value||'').trim();if(!config){showCaseError(id,'Не удалось определить действие. Обновите карточку.');return null}if(comment.length<config.min){const text=`Комментарий должен содержать не менее ${config.min} символов.`;showCaseError(id,text);feedback(text,'bad');return null}caseDrafts.set(draftKey(id,type),comment);return {form,type,comment,config}}
function reviewCaseForm(id){const values=validateCaseComment(id);if(!values)return;clearCaseError(id);document.getElementById('case_review_title_'+id).textContent='Проверка: '+values.config.title;document.getElementById('case_review_effect_'+id).textContent=values.config.effect;document.getElementById('case_review_comment_'+id).textContent=values.comment||'Без комментария';setCaseStage(id,'review')}
function backToCaseEdit(id){setCaseStage(id,'edit');document.getElementById('case_comment_'+id)?.focus()}
async function withAction(key,controls,button,work,label='Сохранение…'){if(pending.has(key))return;pending.add(key);const labels=new Map(controls.filter(x=>x.tagName==='BUTTON').map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent=label;try{return await work()}finally{pending.delete(key);controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((value,x)=>x.textContent=value)}}
async function refreshCaseAfterConflict(id,button){const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'',textarea=document.getElementById('case_comment_'+id);if(type&&textarea)caseDrafts.set(draftKey(id,type),textarea.value);try{await load(button,true);feedback('Карточка обновлена. Черновик сохранён — проверьте действие ещё раз.','warn-text')}catch(e){feedback('Не удалось обновить карточку: '+e.message,'bad')}}
async function submitCaseForm(id,button){const x=caseSnapshot(id),form=document.getElementById('case_form_'+id),type=form?.dataset.type||'',textarea=document.getElementById('case_comment_'+id),comment=(textarea?.value||'').trim();if(!x||!typeAvailable(x,type)){showCaseError(id,'Карточка дела изменилась. Обновите её перед сохранением.',true);return}if(form.dataset.stage!=='review'){showCaseError(id,'Сначала проверьте действие перед сохранением.');return}const config=actionConfigs[type];if(!config||comment.length<config.min){showCaseError(id,'Комментарий не соответствует требованиям действия.');return}caseDrafts.set(draftKey(id,type),comment);const paths={accept:`/lawyer/cases/${id}/accept`,documents:`/lawyer/cases/${id}/request-documents`,transfer:`/lawyer/cases/${id}/transfer-to-m2`,start_claim:`/lawyer/cases/${id}/claim/start`,mark_claim_sent:`/lawyer/cases/${id}/claim/sent`,open_court:`/lawyer/cases/${id}/court/open`};return withAction(`case:${id}`,caseControls(id),button,async()=>{let result;try{result=await api(paths[type],{method:'POST',body:JSON.stringify({comment,expected_status:x.status,expected_updated_at:x.updated_at})})}catch(e){if(e.status===409){caseDrafts.set(draftKey(id,type),comment);showCaseError(id,'Карточка дела изменилась. Черновик сохранён.',true);feedback('Карточка дела изменилась. Черновик сохранён.','warn-text')}else{showCaseError(id,e.message);feedback('Операция не сохранена: '+e.message,'bad')}return}caseDrafts.delete(draftKey(id,type));try{await load(null,true)}catch(e){const text=type==='accept'?'Дело принято, но кабинет не обновился':type==='documents'?'Запрос сохранён, но кабинет не обновился':'Операция сохранена, но кабинет не обновился';feedback(text+': '+e.message,'warn-text');return}const success={accept:'Дело принято. Этап договора открыт.',documents:'Запрос документов отправлен клиенту.',transfer:'Дело переведено в консультационный маршрут.',start_claim:'Подготовка претензии начата. Клиент уведомлён через durable outbox.',mark_claim_sent:'Отправка претензии зафиксирована. 30-дневный срок запущен.',open_court:'Судебный этап открыт. Клиент уведомлён через durable outbox.'};feedback(success[type]||'Операция сохранена.','ok')})}
boot();
</script>
</body>
</html>
"""
