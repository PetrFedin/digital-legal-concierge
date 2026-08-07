from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.user import User
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(prefix="/lawyer/workspace", tags=["lawyer-workspace"])

CLOSED_CASE_STATUSES = {
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


def _route_label(route: str | None) -> str:
    return {
        "M1": "Ведение дела",
        "M2": "Консультация",
    }.get(str(route or ""), "Юридическое обращение")


def _sla_label(value: str | None) -> str:
    return SLA_LABELS.get(str(value or ""), "SLA не определён")


def _case_status(value: object) -> CaseStatus | None:
    try:
        return CaseStatus(str(value))
    except ValueError:
        return None


def _document_status(value: object) -> DocumentStatus | None:
    try:
        return DocumentStatus(str(value))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _consultations_today_count(
    scheduled_values: list[datetime | None],
    *,
    now: datetime,
) -> int:
    today = _as_utc(now).date()
    return sum(
        1
        for scheduled_at in scheduled_values
        if scheduled_at and _as_utc(scheduled_at).date() == today
    )


def _latest_documents(documents: list[Document]) -> dict[str, Document]:
    active = [
        document
        for document in documents
        if _document_status(document.status) != DocumentStatus.ARCHIVED
    ]
    latest: dict[str, Document] = {}
    for document in sorted(
        active,
        key=lambda item: (
            item.document_type,
            -int(item.version or 0),
            -int(item.id or 0),
        ),
    ):
        latest.setdefault(document.document_type, document)
    return latest


def _document_readiness(documents: list[Document]) -> tuple[bool, str | None]:
    latest = _latest_documents(documents)
    ddu = latest.get("DDU")
    if not ddu:
        return False, "Актуальная версия ДДУ не загружена"
    if _document_status(ddu.status) != DocumentStatus.APPROVED:
        return False, "Актуальная версия ДДУ ещё не принята"
    unresolved = [
        document.title
        for document in latest.values()
        if _document_status(document.status) != DocumentStatus.APPROVED
    ]
    if unresolved:
        return False, "Завершите проверку: " + ", ".join(sorted(set(unresolved)))
    return True, None


def _case_priority(
    case: Case,
    *,
    documents_on_review: int,
    documents_ready: bool,
    readiness_reason: str | None,
) -> tuple[str, str, str | None]:
    case_status = _case_status(case.status)
    if str(case.sla_status or "").endswith("OVERDUE"):
        return (
            "critical",
            "Устранить просрочку",
            "Свяжитесь с клиентом и зафиксируйте ближайшее действие",
        )
    if documents_on_review:
        return (
            "high",
            "Проверить документы",
            f"Ожидают решения: {documents_on_review}",
        )
    if case_status == CaseStatus.M1_LAWYER_REVIEW:
        if documents_ready:
            return (
                "high",
                "Принять дело",
                "Все актуальные документы проверены",
            )
        return (
            "high",
            "Завершить проверку документов",
            readiness_reason,
        )
    return (
        "normal",
        case.next_action or "Проверить текущее состояние дела",
        None,
    )


@router.get("/data")
async def workspace_data(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    lawyer_id = actor.lawyer.id

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

    cases: list[dict[str, object]] = []
    overdue_count = 0
    review_count = 0
    actionable_count = 0
    for case, user in case_rows:
        case_status = _case_status(case.status)
        documents = documents_by_case.get(case.id, [])
        documents_on_review = sum(
            1
            for document in documents
            if _document_status(document.status) == DocumentStatus.ON_REVIEW
        )
        documents_ready, readiness_reason = _document_readiness(documents)
        priority, recommended_action, action_note = _case_priority(
            case,
            documents_on_review=documents_on_review,
            documents_ready=documents_ready,
            readiness_reason=readiness_reason,
        )
        is_overdue = str(case.sla_status or "").endswith("OVERDUE")
        can_accept = bool(
            case_status == CaseStatus.M1_LAWYER_REVIEW and documents_ready
        )
        can_request_documents = case_status in {
            CaseStatus.M1_DOCUMENTS_RECEIVED,
            CaseStatus.M1_LAWYER_REVIEW,
        }
        can_transfer_to_m2 = case_status in {
            CaseStatus.M1_DOCUMENTS_PENDING,
            CaseStatus.M1_DOCUMENTS_RECEIVED,
            CaseStatus.M1_LAWYER_REVIEW,
            CaseStatus.M1_DOCS_REQUESTED,
        }
        if is_overdue:
            overdue_count += 1
        review_count += documents_on_review
        if priority in {"critical", "high"}:
            actionable_count += 1
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
                "documents_on_review": documents_on_review,
                "documents_ready": documents_ready,
                "readiness_reason": readiness_reason,
                "priority": priority,
                "recommended_action": recommended_action,
                "action_note": action_note,
                "can_accept": can_accept,
                "can_request_documents": can_request_documents,
                "can_transfer_to_m2": can_transfer_to_m2,
            }
        )

    booked_times = (
        await db.execute(
            select(Consultation.scheduled_at)
            .where(Consultation.lawyer_id == lawyer_id)
            .where(Consultation.status == ConsultationStatus.BOOKED)
            .where(Consultation.scheduled_at.is_not(None))
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    consultations_today = _consultations_today_count(booked_times, now=now)

    return {
        "generated_at": now.isoformat(),
        "lawyer": {
            "id": lawyer_id,
            "name": actor.lawyer.full_name,
        },
        "summary": {
            "active_cases": len(cases),
            "requires_action": actionable_count,
            "documents_on_review": review_count,
            "overdue": overdue_count,
            "consultations_today": consultations_today,
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
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Рабочий кабинет юриста — Digital Legal Concierge</title>
<style>
:root{
  --bg:#f4f6fa;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;
  --primary:#3157d5;--primary-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;
  --red:#b42318;--red-soft:#fef3f2;--amber:#a15c00;--amber-soft:#fff7e6;
  --shadow:0 12px 34px rgba(16,24,40,.07)
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}
header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:20px 24px}
header .inner{max-width:1240px;margin:auto;display:flex;justify-content:space-between;gap:18px;align-items:center}
h1{font-size:23px;margin:0 0 4px} header p{margin:0;color:#d0d5dd;font-size:13px}
.links,.row,.tabs,.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.button,button{border:0;border-radius:10px;padding:9px 12px;background:var(--primary);color:#fff;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}
.secondary{background:#475467}.green{background:var(--green)}.amber{background:var(--amber)}.red{background:var(--red)}
button:disabled,textarea:disabled,select:disabled{opacity:.55;cursor:wait}
main{max-width:1240px;margin:auto;padding:22px}
.welcome,.toolbar{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:14px}
.welcome h2{margin:0 0 4px}.muted{color:var(--muted);font-size:13px}
.metric-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:11px}
.metric{background:#fff;border:1px solid var(--line);border-radius:15px;padding:14px;box-shadow:var(--shadow);text-align:left;color:var(--ink);text-decoration:none}
.metric b{display:block;font-size:26px;margin-bottom:3px}.metric span{color:var(--muted);font-size:12px}
.metric.urgent{background:var(--red-soft);border-color:#fecdca}.metric.warn{background:var(--amber-soft);border-color:#fedf89}
.metric.consult{background:var(--green-soft);border-color:#abefc6}.metric.consult:hover,.metric.consult:focus{outline:2px solid #6ce9a6;outline-offset:2px}
.toolbar{margin:20px 0 10px;align-items:center}.tabs button{background:#fff;color:var(--ink);border:1px solid var(--line)}.tabs button.active{background:var(--primary);color:#fff;border-color:var(--primary)}
.handoff{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:12px}.handoff strong{color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:17px;padding:16px;box-shadow:var(--shadow)}
.card.critical{border-color:#fda29b;background:#fffafa}.card.high{border-color:#fedf89}
.card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.card h3{margin:0 0 4px;font-size:17px}
.badge{display:inline-flex;border-radius:999px;padding:5px 9px;background:#eef2f6;font-size:12px;font-weight:750}.badge.red{background:var(--red-soft);color:var(--red)}.badge.amber{background:var(--amber-soft);color:var(--amber)}
.action-box{background:var(--primary-soft);border:1px solid #c7d2fe;border-radius:12px;padding:12px;margin:12px 0}.action-box b{display:block;margin-bottom:4px}
.meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.cell{background:#f8fafc;border-radius:10px;padding:9px}.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}
.form{display:none;border-top:1px solid var(--line);margin-top:12px;padding-top:12px}.form.open{display:block}
.form textarea{width:100%;border:1px solid #d0d5dd;border-radius:10px;padding:9px;margin:5px 0;min-height:86px}
.message{min-height:22px;margin:10px 0}.ok{color:var(--green)}.bad{color:var(--red)}.warn-text{color:var(--amber)}
.empty,.error,.loading{grid-column:1/-1;padding:32px;text-align:center;border:1px dashed var(--line);border-radius:15px;background:#fff;color:var(--muted)}.error{background:var(--red-soft);color:var(--red)}
@media(max-width:980px){.metric-grid{grid-template-columns:repeat(3,1fr)}.grid{grid-template-columns:1fr}}
@media(max-width:720px){.handoff{align-items:flex-start;flex-direction:column}}
@media(max-width:620px){header .inner,.welcome,.toolbar{align-items:flex-start;flex-direction:column}.metric-grid{grid-template-columns:1fr 1fr}main{padding:14px}.meta{grid-template-columns:1fr}.links{width:100%}.links .button{flex:1;text-align:center}}
</style>
</head>
<body>
<header>
  <div class="inner">
    <div>
      <h1>⚖ Рабочий кабинет юриста</h1>
      <p>Дела и сроки — консультации ведутся в отдельной рабочей очереди</p>
    </div>
    <div class="links">
      <a class="button green" href="/lawyer/consultation-desk/ui">Консультации</a>
      <a class="button secondary" href="/document-access/review/ui">Проверка документов</a>
      <a class="button secondary" href="/message-center/ui">Сообщения</a>
      <a class="button secondary" href="/operator">Все разделы</a>
      <form method="post" action="/logout" style="margin:0"><button class="secondary" type="submit">Выйти</button></form>
    </div>
  </div>
</header>
<main>
  <div class="welcome">
    <div><h2 id="lawyerName">Рабочий день</h2><div id="freshness" class="muted"></div></div>
    <button class="secondary" onclick="load(this)">Обновить</button>
  </div>
  <div id="metrics" class="metric-grid"></div>
  <div class="toolbar">
    <div class="tabs">
      <button class="active" onclick="showTab('priority',this)">Требуют действий</button>
      <button onclick="showTab('cases',this)">Все дела</button>
    </div>
    <div class="handoff">
      <span><strong>Консультации:</strong> подготовка, встреча, результат и неявки работают в отдельной очереди с временными ограничениями.</span>
      <a class="button green" href="/lawyer/consultation-desk/ui">Открыть консультации →</a>
    </div>
  </div>
  <div id="message" class="message" role="status" aria-live="polite"></div>
  <div id="content" class="grid"><div class="loading">Загрузка кабинета…</div></div>
</main>
<script>
let token='',data=null,currentTab='priority';
const pending=new Set(),caseDrafts=new Map();
const content=document.getElementById('content'),message=document.getElementById('message'),metrics=document.getElementById('metrics'),freshness=document.getElementById('freshness'),lawyerName=document.getElementById('lawyerName');
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}
function feedback(text,state=''){message.textContent=text;message.className='message '+state}
function dt(v){return v?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—'}
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';throw new Error('Сессия истекла или недостаточно прав')}const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка запроса');return d}
async function boot(){try{const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('lawyer'))throw new Error('Требуется роль юриста');token=s.api_token||'';await load()}catch(e){showError(e)}}
function showError(e){content.innerHTML=`<div class="error"><b>Не удалось загрузить кабинет</b><p>${esc(e.message||e)}</p><button onclick="load()">Повторить</button></div>`;feedback(e.message||String(e),'bad')}
async function load(button=null,propagate=false){if(button){button.disabled=true;button.setAttribute('aria-busy','true')}content.innerHTML='<div class="loading">Загрузка кабинета…</div>';if(!propagate)feedback('');try{data=await api('/lawyer/workspace/data');lawyerName.textContent=data.lawyer?.name||'Рабочий день';freshness.textContent='Обновлено '+dt(data.generated_at);renderMetrics();render()}catch(e){if(propagate)throw e;showError(e)}finally{if(button){button.disabled=false;button.removeAttribute('aria-busy')}}}
function renderMetrics(){const s=data.summary||{};metrics.innerHTML=`<div class="metric"><b>${s.active_cases||0}</b><span>активных дел</span></div><div class="metric warn"><b>${s.requires_action||0}</b><span>требуют действия</span></div><div class="metric warn"><b>${s.documents_on_review||0}</b><span>документов на проверке</span></div><div class="metric ${s.overdue?'urgent':''}"><b>${s.overdue||0}</b><span>SLA-просрочек</span></div><a class="metric consult" href="/lawyer/consultation-desk/ui" aria-label="Открыть консультации на сегодня"><b>${s.consultations_today||0}</b><span>консультаций сегодня · открыть →</span></a>`}
function showTab(tab,button){currentTab=tab;document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('active',x===button));render()}
function render(){if(!data)return;let cases=data.cases||[];if(currentTab==='priority')cases=cases.filter(x=>['critical','high'].includes(x.priority));const html=cases.map(caseCard).join('');content.innerHTML=html||empty(currentTab==='priority'?'Срочных действий нет':'Активных дел нет','Очередь на текущий момент обработана.')}
function empty(title,detail){return `<div class="empty"><b>${esc(title)}</b><p>${esc(detail)}</p></div>`}
function caseControls(id){return Array.from(document.querySelectorAll(`[data-case-id="${id}"]`))}
async function withAction(key,controls,button,work,label='Выполняется…'){if(pending.has(key))return;pending.add(key);const labels=new Map(controls.filter(x=>x.tagName==='BUTTON').map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent=label;try{return await work()}finally{pending.delete(key);controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((value,x)=>x.textContent=value)}}
function caseCard(x){const overdue=String(x.sla_status||'').includes('OVERDUE');const buttons=[`<a class="button secondary" href="/message-center/ui?case_id=${x.case_id}">Переписка</a>`];if(x.documents_on_review)buttons.unshift(`<a class="button amber" href="/document-access/review/ui">Проверить документы (${x.documents_on_review})</a>`);if(x.can_accept)buttons.unshift(`<button class="green" data-case-id="${x.case_id}" onclick="openCaseForm(${x.case_id},'accept')">Принять дело</button>`);if(x.can_request_documents)buttons.push(`<button class="amber" data-case-id="${x.case_id}" onclick="openCaseForm(${x.case_id},'documents')">Запросить документы</button>`);if(x.can_transfer_to_m2)buttons.push(`<button class="red" data-case-id="${x.case_id}" onclick="openCaseForm(${x.case_id},'transfer')">Перевести в консультацию</button>`);return `<article class="card ${esc(x.priority)}" id="case_${x.case_id}"><div class="card-head"><div><h3>${esc(x.case_number)}</h3><div>${esc(x.client_name)} · ${esc(x.route_label)}</div></div><span class="badge ${overdue?'red':x.priority==='high'?'amber':''}">${esc(x.status_label)}</span></div><div class="action-box"><b>Ближайшее действие</b>${esc(x.recommended_action)}${x.action_note?`<div class="muted">${esc(x.action_note)}</div>`:''}</div><div class="meta"><div class="cell"><span>SLA</span>${esc(x.sla_label)}</div><div class="cell"><span>Срок</span>${dt(x.sla_due_at)}</div><div class="cell"><span>Документы на проверке</span>${esc(x.documents_on_review)}</div><div class="cell"><span>Обновлено</span>${dt(x.updated_at)}</div></div><div class="actions" style="margin-top:12px">${buttons.join('')}</div><div class="form" id="case_form_${x.case_id}"><h4 id="case_form_title_${x.case_id}"></h4><textarea id="case_comment_${x.case_id}" oninput="rememberCaseDraft(${x.case_id},this)" placeholder="Комментарий клиенту"></textarea><div class="actions"><button data-case-id="${x.case_id}" onclick="submitCaseForm(${x.case_id},this)">Подтвердить</button><button class="secondary" onclick="closeCaseForm(${x.case_id})">Отмена</button></div></div></article>`}
function draftKey(id,type){return `${id}:${type}`}
function rememberCaseDraft(id,textarea){const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'';if(type)caseDrafts.set(draftKey(id,type),textarea.value)}
function openCaseForm(id,type){document.querySelectorAll('.form').forEach(x=>x.classList.remove('open'));const titles={accept:'Основание принятия дела',documents:'Какие документы запросить',transfer:'Почему дело переводится в консультацию'};const form=document.getElementById('case_form_'+id);form.dataset.type=type;document.getElementById('case_form_title_'+id).textContent=titles[type]||'Комментарий';const textarea=document.getElementById('case_comment_'+id);textarea.value=caseDrafts.get(draftKey(id,type))||'';form.classList.add('open');textarea.focus()}
function closeCaseForm(id){document.getElementById('case_form_'+id).classList.remove('open')}
function caseSnapshot(id){return (data.cases||[]).find(x=>x.case_id===id)}
async function submitCaseForm(id,button){const x=caseSnapshot(id),form=document.getElementById('case_form_'+id),type=form.dataset.type,comment=document.getElementById('case_comment_'+id).value.trim();if(!x){feedback('Карточка дела устарела. Обновите кабинет.','bad');return}const min=type==='transfer'?10:5;if(comment.length<min){feedback(`Комментарий должен содержать не менее ${min} символов.`,'bad');return}const paths={accept:`/lawyer/cases/${id}/accept`,documents:`/lawyer/cases/${id}/request-documents`,transfer:`/lawyer/cases/${id}/transfer-to-m2`};const body=type==='transfer'?{reason:comment,expected_status:x.status,expected_updated_at:x.updated_at}:{comment,expected_status:x.status,expected_updated_at:x.updated_at};const confirmations={accept:'Принять дело и открыть этап договора?',documents:'Отправить клиенту запрос документов?',transfer:'Перевести дело в консультационный маршрут?'};if(!confirm(confirmations[type]))return;return withAction('case:'+id,caseControls(id),button,async()=>{try{const result=await api(paths[type],{method:'POST',body:JSON.stringify(body)});const success={accept:'Дело принято. Открыт этап договора: '+result.status,documents:'Запрос документов отправлен.',transfer:'Дело переведено в консультационный маршрут.'};caseDrafts.delete(draftKey(id,type));try{await load(null,true);feedback(success[type],'ok')}catch(e){const saved={accept:'Дело принято, но кабинет не обновился: ',documents:'Запрос сохранён, но кабинет не обновился: ',transfer:'Операция сохранена, но кабинет не обновился: '};feedback(saved[type]+e.message,'warn-text')}}catch(e){feedback('Операция не выполнена: '+e.message,'bad')}},'Сохранение…')}
boot();
</script>
</body>
</html>
"""
