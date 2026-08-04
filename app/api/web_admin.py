from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.db.session import get_db
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document

router = APIRouter(tags=["web-admin"])

CLOSED_STATUSES = {"M1_CLOSED", "M2_CLOSED", "ARCHIVED"}
DOCUMENT_REVIEW_STATUSES = {"UPLOADED", "PENDING", "REVIEW_PENDING", "NEEDS_REVIEW"}
QUEUE_NAMES = {"unassigned", "documents", "consultations", "overdue"}


def _case_row(case: Case, *, queue: str) -> dict[str, object]:
    return {
        "id": case.id,
        "number": case.case_number,
        "route": case.route,
        "status": case.status,
        "lawyer_id": case.assigned_lawyer_id,
        "next_action": case.next_action,
        "sla_status": case.sla_status,
        "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
        "created_at": case.created_at.isoformat(),
        "queue": queue,
    }


@router.get("/admin/work-queues/{queue_name}")
async def work_queue(
    queue_name: str,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    if queue_name not in QUEUE_NAMES:
        raise HTTPException(status_code=404, detail="Неизвестная рабочая очередь")

    stmt = select(Case).where(Case.status.notin_(CLOSED_STATUSES))
    if queue_name == "unassigned":
        stmt = stmt.where(Case.assigned_lawyer_id.is_(None)).order_by(Case.created_at.asc())
    elif queue_name == "documents":
        stmt = (
            stmt.join(Document, Document.case_id == Case.id)
            .where(Document.status.in_(DOCUMENT_REVIEW_STATUSES))
            .order_by(Case.created_at.asc())
        )
    elif queue_name == "consultations":
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999999)
        stmt = (
            stmt.join(Consultation, Consultation.case_id == Case.id)
            .where(Consultation.status == "BOOKED")
            .where(Consultation.scheduled_at >= day_start)
            .where(Consultation.scheduled_at <= day_end)
            .order_by(Consultation.scheduled_at.asc())
        )
    else:
        stmt = stmt.where(
            Case.sla_status.in_(["FIRST_RESPONSE_OVERDUE", "ACTION_OVERDUE"])
        ).order_by(Case.sla_due_at.asc())

    result = await db.execute(stmt.limit(200))
    cases = list(result.scalars().unique().all())
    return {
        "queue": queue_name,
        "count": len(cases),
        "items": [_case_row(case, queue=queue_name) for case in cases],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/admin-ui", response_class=HTMLResponse)
async def admin_ui():
    return HTMLResponse(ADMIN_HTML)


ADMIN_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Digital Legal Concierge — рабочий кабинет</title>
<style>
:root{--bg:#f4f6fa;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--primary:#3157d5;--primary-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fef3f2;--amber:#a15c00;--amber-soft:#fff7e6;--shadow:0 10px 30px rgba(16,24,40,.07)}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center;gap:16px}header h1{font-size:20px;margin:0 0 4px}header p{margin:0;color:#d0d5dd;font-size:13px}.layout{display:grid;grid-template-columns:230px minmax(0,1fr) 350px;min-height:calc(100vh - 76px)}nav,.side{background:var(--surface);padding:18px}.side{border-left:1px solid var(--line);overflow:auto}nav{border-right:1px solid var(--line)}.content{padding:22px;overflow:auto}.nav-title{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:14px 10px 6px}.nav-button{display:block;width:100%;text-align:left;border:0;background:transparent;color:var(--ink);border-radius:10px;padding:10px 11px;cursor:pointer;font-weight:650}.nav-button:hover,.nav-button.active{background:var(--primary-soft);color:#2445b5}.header-actions,.toolbar,.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.toolbar{justify-content:space-between;margin-bottom:14px}.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:var(--shadow)}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric{border:1px solid var(--line);background:#fff;border-radius:14px;padding:15px;cursor:pointer;text-align:left}.metric:hover{border-color:#b8c4f4;background:var(--primary-soft)}.metric b{display:block;font-size:27px;margin-bottom:4px}.metric span{font-size:13px;color:var(--muted)}.metric.urgent{background:var(--red-soft);border-color:#fecdca}.metric.warn{background:var(--amber-soft);border-color:#fedf89}.section-title{display:flex;justify-content:space-between;align-items:end;gap:12px;margin:22px 0 10px}.section-title h2{margin:0;font-size:20px}.section-title p{margin:0;color:var(--muted);font-size:13px}.queue-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.queue-card{border:1px solid var(--line);background:#fff;border-radius:14px;padding:15px;cursor:pointer}.queue-card:hover{border-color:#b8c4f4;transform:translateY(-1px)}.queue-card b{font-size:17px}.queue-card .count{float:right;font-size:22px;font-weight:800}.list-item{border:1px solid var(--line);border-radius:13px;padding:13px;margin:9px 0;background:#fff}.list-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.badge{display:inline-flex;padding:4px 8px;border-radius:999px;background:#eef2f6;font-size:12px;font-weight:700}.badge.red{color:var(--red);background:var(--red-soft)}.badge.amber{color:var(--amber);background:var(--amber-soft)}.muted{font-size:13px;color:var(--muted)}.ok{color:var(--green)}.bad{color:var(--red)}.warn-text{color:var(--amber)}button,.button{border:0;border-radius:9px;padding:9px 12px;background:var(--primary);color:#fff;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}button.secondary,.button.secondary{background:#475467}button.green{background:var(--green)}button:disabled{opacity:.55;cursor:wait}.empty,.error,.loading{padding:28px;text-align:center;border:1px dashed var(--line);border-radius:14px;color:var(--muted)}.error{color:var(--red);background:var(--red-soft)}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;color:var(--muted);text-transform:uppercase}input,select,textarea{width:100%;padding:9px;border:1px solid #d0d5dd;border-radius:9px;margin:5px 0}textarea{min-height:90px;resize:vertical}.raw-toggle{font-size:12px;color:var(--muted);cursor:pointer}pre{white-space:pre-wrap;word-break:break-word;background:#111827;color:#e5e7eb;padding:12px;border-radius:10px;max-height:240px;overflow:auto}.side h3{margin-top:0}.side-section{border-top:1px solid var(--line);padding-top:14px;margin-top:14px}@media(max-width:1120px){.layout{grid-template-columns:210px 1fr}.side{grid-column:1/-1;border-left:0;border-top:1px solid var(--line)}}@media(max-width:760px){header{align-items:flex-start;flex-direction:column}.layout{display:block}nav{display:flex;gap:6px;overflow:auto;padding:10px;border-right:0}.nav-title{display:none}.nav-button{white-space:nowrap;width:auto}.content{padding:14px}.metric-grid,.queue-grid{grid-template-columns:1fr 1fr}.side{padding:14px}}@media(max-width:500px){.metric-grid,.queue-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><div><h1>⚖ Digital Legal Concierge</h1><p>Операционный кабинет администратора</p></div><div class="header-actions"><a class="button secondary" href="/message-center/ui">Сообщения</a><a class="button secondary" href="/admin/sla/ui">SLA</a><a class="button secondary" href="/operator">Все разделы</a><form method="post" action="/logout" style="margin:0"><button class="secondary" type="submit">Выйти</button></form></div></header>
<div class="layout">
<nav><div class="nav-title">Работа</div><button class="nav-button active" data-tab="dashboard" onclick="showTab('dashboard',this)">Обзор</button><button class="nav-button" data-tab="queue" onclick="showTab('queue',this)">Рабочие очереди</button><button class="nav-button" data-tab="cases" onclick="showTab('cases',this)">Все дела</button><button class="nav-button" data-tab="documents" onclick="showTab('documents',this)">Документы</button><div class="nav-title">Команда</div><button class="nav-button" data-tab="lawyers" onclick="showTab('lawyers',this)">Юристы</button><button class="nav-button" data-tab="notifications" onclick="showTab('notifications',this)">Уведомления</button><div class="nav-title">Система</div><button class="nav-button" data-tab="payments" onclick="showTab('payments',this)">Платежи</button><button class="nav-button" data-tab="settings" onclick="showTab('settings',this)">Настройки</button></nav>
<main class="content"><div class="toolbar"><div><b id="pageTitle">Обзор</b><div id="freshness" class="muted"></div></div><div class="row"><button class="secondary" onclick="reloadCurrent(this)">Обновить</button><button data-global-action="scheduler" class="green" onclick="runScheduler(this)">Запустить проверки</button></div></div><div id="message" class="muted" role="status" aria-live="polite"></div><div id="view" class="card"><div class="loading">Загрузка…</div></div></main>
<aside class="side"><h3>Карточка дела</h3><div id="side" class="muted">Откройте дело из очереди или списка.</div><div class="side-section"><span class="raw-toggle" onclick="toggleRaw()">Технический ответ API</span><pre id="raw" hidden>{}</pre></div></aside>
</div>
<script>
let currentTab='dashboard',currentQueue='unassigned',currentCase=null,loadController=null;const pending=new Set();
const view=document.getElementById('view'),side=document.getElementById('side'),raw=document.getElementById('raw'),message=document.getElementById('message'),freshness=document.getElementById('freshness'),pageTitle=document.getElementById('pageTitle');let apiToken='';
const titles={dashboard:'Обзор',queue:'Рабочие очереди',cases:'Все дела',documents:'Документы',lawyers:'Юристы',notifications:'Уведомления',payments:'Платежи',settings:'Настройки'};
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}
function feedback(text,state='muted'){message.textContent=text;message.className=state}
function compactError(e){return e&&e.message?e.message:String(e)}
function toggleRaw(){raw.hidden=!raw.hidden}
function loading(text='Загрузка…'){view.innerHTML=`<div class="loading">${esc(text)}</div>`}
function empty(title,detail=''){return `<div class="empty"><b>${esc(title)}</b>${detail?`<p>${esc(detail)}</p>`:''}</div>`}
function errorState(e){view.innerHTML=`<div class="error"><b>Не удалось загрузить раздел</b><p>${esc(compactError(e))}</p><button onclick="loadCurrent()">Повторить</button></div>`}
function formatDate(v){if(!v)return '—';try{return new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v))}catch{return v}}
async function api(path,opts={}){if(!apiToken)throw new Error('Административная сессия не загружена');const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':apiToken,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';throw new Error('Сессия истекла')}const data=await r.json().catch(()=>({}));raw.textContent=JSON.stringify(data,null,2);if(!r.ok){const e=new Error(data.detail||'Ошибка запроса');e.status=r.status;throw e}return data}
async function withAction(key,button,work,label='Выполняется…'){if(pending.has(key))return;pending.add(key);const old=button?.textContent;if(button){button.disabled=true;button.textContent=label}try{return await work()}finally{pending.delete(key);if(button){button.disabled=false;button.textContent=old}}}
async function boot(){try{const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const session=await r.json();if(!(session.roles||[session.role]).includes('admin')){throw new Error('Требуется роль администратора')}apiToken=session.api_token||'';await loadCurrent()}catch(e){errorState(e)}}
function showTab(name,button){currentTab=name;pageTitle.textContent=titles[name]||name;document.querySelectorAll('.nav-button').forEach(x=>x.classList.toggle('active',x===button));side.innerHTML='Откройте дело из очереди или списка.';void loadCurrent()}
async function reloadCurrent(button){return withAction('reload:'+currentTab,button,()=>loadCurrent(),'Обновление…')}
async function loadCurrent(){if(loadController)loadController.abort();const controller=new AbortController();loadController=controller;loading();feedback('');try{if(currentTab==='dashboard')await loadDashboard(controller);else if(currentTab==='queue')await loadQueue(currentQueue,controller);else if(currentTab==='cases')await loadCases(controller);else if(currentTab==='documents')await loadDocuments(controller);else if(currentTab==='lawyers')await loadLawyers(controller);else if(currentTab==='notifications')await loadNotifications(controller);else if(currentTab==='payments')await loadPayments(controller);else await loadSettings(controller)}catch(e){if(e.name!=='AbortError')errorState(e)}finally{if(loadController===controller)loadController=null}}
function queueCard(key,title,count,detail,urgent=false){return `<button class="queue-card ${urgent?'urgent':''}" onclick="openQueue('${key}')"><span class="count">${count||0}</span><b>${esc(title)}</b><div class="muted">${esc(detail)}</div></button>`}
async function loadDashboard(controller){const d=await api('/admin/dashboard',{signal:controller.signal});freshness.textContent='Обновлено '+formatDate(d.generated_at);view.innerHTML=`<div class="metric-grid"><button class="metric" onclick="showCasesTab()"><b>${d.cases?.active||0}</b><span>активных дел</span></button><button class="metric warn" onclick="openQueue('unassigned')"><b>${d.queue?.unassigned||0}</b><span>без юриста</span></button><button class="metric ${d.queue?.overdue?'urgent':''}" onclick="openQueue('overdue')"><b>${d.queue?.overdue||0}</b><span>SLA-просрочек</span></button><button class="metric" onclick="openQueue('consultations')"><b>${d.consultations?.today||0}</b><span>консультаций сегодня</span></button></div><div class="section-title"><div><h2>Что требует внимания</h2><p>Откройте очередь и выполните ближайшее действие</p></div></div><div class="queue-grid">${queueCard('unassigned','Назначить юриста',d.queue?.unassigned,'Новые дела без ответственного')}${queueCard('documents','Проверить документы',d.queue?.documents_review,'Файлы ожидают юридической проверки')}${queueCard('consultations','Консультации сегодня',d.queue?.consultations_today,'Подготовка и проведение консультаций')}${queueCard('overdue','Просроченные действия',d.queue?.overdue,'Требуется немедленная реакция',Boolean(d.queue?.overdue))}</div>`}
function showCasesTab(){const button=document.querySelector('[data-tab="cases"]');showTab('cases',button)}
function openQueue(name){currentQueue=name;const button=document.querySelector('[data-tab="queue"]');showTab('queue',button)}
const queueTitles={unassigned:'Дела без юриста',documents:'Документы на проверке',consultations:'Консультации сегодня',overdue:'Просроченные действия'};
async function loadQueue(name,controller){const d=await api('/admin/work-queues/'+encodeURIComponent(name),{signal:controller.signal});freshness.textContent='Обновлено '+formatDate(d.generated_at);const tabs=Object.entries(queueTitles).map(([key,title])=>`<button class="${key===name?'':'secondary'}" onclick="openQueue('${key}')">${esc(title)}</button>`).join(' ');const items=(d.items||[]).map(x=>`<article class="list-item"><div class="list-head"><div><b>${esc(x.number)}</b> · ${esc(x.route||'—')}<div class="muted">Создано ${formatDate(x.created_at)}</div></div><span class="badge ${x.sla_status?.includes('OVERDUE')?'red':''}">${esc(x.status)}</span></div><p>${esc(x.next_action||'Определить следующее действие')}</p><div class="muted">Юрист: ${esc(x.lawyer_id||'не назначен')} · SLA: ${esc(x.sla_status||'не запущен')} ${x.sla_due_at?'до '+formatDate(x.sla_due_at):''}</div><div class="row" style="margin-top:10px"><button onclick="openCase(${x.id})">Открыть дело</button>${!x.lawyer_id?`<button class="green" data-expected-status="${esc(x.status)}" onclick="autoAssign(${x.id},this)">Автоназначить</button>`:''}</div></article>`).join('');view.innerHTML=`<div class="row">${tabs}</div><div class="section-title"><div><h2>${esc(queueTitles[name])}</h2><p>${d.count} элементов</p></div></div>${items||empty('Очередь пуста','На текущий момент действий в этой категории нет.')}`}
async function loadCases(controller){const rows=await api('/admin/cases',{signal:controller.signal});freshness.textContent='';if(!rows.length){view.innerHTML=empty('Дел пока нет');return}view.innerHTML='<table><tr><th>Дело</th><th>Маршрут</th><th>Статус</th><th>Юрист</th><th>Действие</th></tr>'+rows.map(x=>`<tr><td><b>${esc(x.number)}</b><br><span class="muted">#${x.id}</span></td><td>${esc(x.route||'—')}</td><td><span class="badge">${esc(x.status)}</span></td><td>${esc(x.lawyer_id||'не назначен')}</td><td><button onclick="openCase(${x.id})">Открыть</button></td></tr>`).join('')+'</table>'}
async function openCase(id){try{side.innerHTML='<div class="loading">Загрузка карточки…</div>';const d=await api('/admin/cases/'+id);currentCase=d.case;const docs=(d.documents||[]).map(x=>`<div class="list-item"><b>${esc(x.title)}</b><div class="muted">${esc(x.status)}</div></div>`).join('')||empty('Документов нет');const payments=(d.payments||[]).map(p=>`<div class="list-item"><b>${esc(p.title)}</b><div class="muted">${p.amount} · ${esc(p.status)}</div>${p.manual_confirm_allowed?`<button class="green" data-expected-status="${esc(p.status)}" onclick="confirmPayment(${p.id},${id},this)">Подтвердить тестовый платёж</button>`:''}</div>`).join('');side.innerHTML=`<h3>${esc(d.case.number)}</h3><span class="badge">${esc(d.case.status)}</span><p><b>Следующее действие</b><br>${esc(d.case.next_action||'Определить следующий шаг')}</p><div class="muted">Маршрут: ${esc(d.case.route||'—')}<br>Юрист: ${esc(d.case.lawyer_id||'не назначен')}</div><div class="row" style="margin-top:12px"><button onclick="showStatusForm(${id})">Изменить статус</button>${d.case.lawyer_id?'':`<button class="green" data-expected-status="${esc(d.case.status)}" onclick="autoAssign(${id},this)">Автоназначить</button>`}</div><div class="side-section"><h4>Документы</h4>${docs}</div>${payments?`<div class="side-section"><h4>Платежи</h4>${payments}</div>`:''}`;return d}catch(e){side.innerHTML=`<div class="error">${esc(compactError(e))}<br><button onclick="openCase(${id})">Повторить</button></div>`}}
async function showStatusForm(id){try{const statuses=await api('/admin/statuses');const d=currentCase&&currentCase.id===id?{case:currentCase}:await openCase(id);const c=d.case||currentCase;side.insertAdjacentHTML('beforeend',`<div class="side-section"><h4>Изменить статус</h4><select id="newStatus">${statuses.map(s=>`<option value="${esc(s)}" ${s===c.status?'selected':''}>${esc(s)}</option>`).join('')}</select><textarea id="statusComment" placeholder="Причина изменения"></textarea><button data-expected-status="${esc(c.status)}" onclick="saveStatus(${id},this)">Сохранить</button></div>`)}catch(e){feedback(compactError(e),'bad')}}
async function saveStatus(id,button){const next=document.getElementById('newStatus')?.value||'',comment=(document.getElementById('statusComment')?.value||'').trim(),expected=button.dataset.expectedStatus||'';if(comment.length<5){feedback('Укажите причину изменения — не менее 5 символов','bad');return}if(next===expected){feedback('Выберите новый статус','bad');return}if(!confirm(`Изменить статус ${expected} → ${next}?`))return;return withAction('case:'+id,button,async()=>{try{await api('/admin/cases/'+id+'/status',{method:'POST',body:JSON.stringify({status:next,comment,expected_status:expected})});feedback('Статус сохранён','ok');await openCase(id);await loadCurrent()}catch(e){feedback('Статус не изменён: '+compactError(e),'bad')}},'Сохранение…')}
async function autoAssign(id,button){const expected=button.dataset.expectedStatus||'';if(!confirm('Автоматически назначить подходящего юриста?'))return;return withAction('assign:'+id,button,async()=>{try{await api('/admin/cases/'+id+'/auto-assign',{method:'POST',body:JSON.stringify({expected_status:expected,expected_lawyer_id:null})});feedback('Юрист назначен','ok');await openCase(id);await loadCurrent()}catch(e){feedback('Назначение не выполнено: '+compactError(e),'bad')}},'Назначение…')}
async function loadDocuments(controller){const rows=await api('/admin/documents',{signal:controller.signal});if(!rows.length){view.innerHTML=empty('Документов пока нет');return}view.innerHTML='<table><tr><th>Дело</th><th>Тип</th><th>Файл</th><th>Статус</th></tr>'+rows.map(x=>`<tr><td><button onclick="openCase(${x.case_id})">#${x.case_id}</button></td><td>${esc(x.type)}</td><td>${esc(x.file_name)}</td><td><span class="badge">${esc(x.status)}</span></td></tr>`).join('')+'</table>'}
async function loadPayments(controller){const rows=await api('/admin/payments',{signal:controller.signal});if(!rows.length){view.innerHTML=empty('Платежей нет','В режиме без онлайн-оплаты это нормальное состояние.');return}view.innerHTML='<table><tr><th>ID</th><th>Дело</th><th>Назначение</th><th>Сумма</th><th>Статус</th></tr>'+rows.map(p=>`<tr><td>${p.id}</td><td><button onclick="openCase(${p.case_id})">#${p.case_id}</button></td><td>${esc(p.title)}</td><td>${p.amount}</td><td>${esc(p.status)}</td></tr>`).join('')+'</table>'}
async function confirmPayment(paymentId,caseId,button){const expected=button.dataset.expectedStatus||'';if(!confirm('Подтвердить тестовый платёж?'))return;return withAction('payment:'+paymentId,button,async()=>{try{await api('/admin/payments/'+paymentId+'/confirm',{method:'POST',body:JSON.stringify({expected_status:expected})});feedback('Платёж подтверждён','ok');await openCase(caseId);await loadCurrent()}catch(e){feedback(compactError(e),'bad')}},'Подтверждение…')}
async function loadLawyers(controller){const rows=await api('/admin/lawyers',{signal:controller.signal});view.innerHTML='<div class="row"><button onclick="showLawyerForm()">Добавить юриста</button></div>'+(rows.length?'<table><tr><th>ФИО</th><th>Email</th><th>Статус</th><th>Лимит</th></tr>'+rows.map(x=>`<tr><td>${esc(x.full_name)}</td><td>${esc(x.email||'—')}</td><td>${x.is_active?'активен':'отключён'}</td><td>${x.workload_limit}</td></tr>`).join('')+'</table>':empty('Юристы не добавлены'))}
function showLawyerForm(){side.innerHTML=`<h3>Новый юрист</h3><input id="lawyerName" placeholder="ФИО"><input id="lawyerEmail" type="email" placeholder="Email"><input id="lawyerPhone" placeholder="Телефон"><input id="lawyerSpec" placeholder="Специализация"><input id="lawyerLimit" type="number" min="1" max="500" value="30"><button onclick="createLawyer(this)">Создать</button>`}
async function createLawyer(button){const full_name=(document.getElementById('lawyerName')?.value||'').trim(),email=(document.getElementById('lawyerEmail')?.value||'').trim().toLowerCase(),workload_limit=Number(document.getElementById('lawyerLimit')?.value||0);if(full_name.length<3||!email.includes('@')||!Number.isInteger(workload_limit)){feedback('Проверьте ФИО, email и лимит','bad');return}return withAction('create-lawyer',button,async()=>{try{await api('/admin/lawyers',{method:'POST',body:JSON.stringify({full_name,email,phone:document.getElementById('lawyerPhone')?.value||'',specialization:document.getElementById('lawyerSpec')?.value||'',workload_limit})});feedback('Юрист создан','ok');side.innerHTML='Юрист создан.';await loadCurrent()}catch(e){feedback(compactError(e),'bad')}},'Создание…')}
async function loadNotifications(controller){const rows=await api('/admin/notifications',{signal:controller.signal});view.innerHTML=rows.length?rows.map(x=>`<article class="list-item"><b>${esc(x.title)}</b> · <span class="badge">${esc(x.status)}</span><p>${esc(x.text)}</p><div class="muted">${esc(x.event)} · дело ${esc(x.case_id||'—')}</div></article>`).join(''):empty('Новых уведомлений нет')}
async function loadSettings(controller){const rows=await api('/admin/settings',{signal:controller.signal});view.innerHTML=rows.length?rows.map((r,i)=>`<article class="list-item"><b>${esc(r.title)}</b><div class="muted">${esc(r.key)} · ${formatDate(r.updated_at)}</div><input id="setting_${i}" value="${esc(r.value?.value??'')}" ${r.editable?'':'disabled'}>${r.editable?`<button data-key="${esc(r.key)}" data-input="setting_${i}" data-updated="${esc(r.updated_at)}" onclick="saveSetting(this)">Сохранить</button>`:''}</article>`).join(''):empty('Настройки не найдены')}
async function saveSetting(button){const key=button.dataset.key,input=document.getElementById(button.dataset.input);if(!key||!input)return;if(!confirm('Сохранить системную настройку '+key+'?'))return;return withAction('setting:'+key,button,async()=>{try{const r=await api('/admin/settings/'+encodeURIComponent(key),{method:'POST',body:JSON.stringify({value:input.value,expected_updated_at:button.dataset.updated})});button.dataset.updated=r.updated_at;feedback('Настройка сохранена','ok')}catch(e){feedback(compactError(e),'bad')}},'Сохранение…')}
async function runScheduler(button){if(!confirm('Запустить плановые проверки сейчас?'))return;return withAction('scheduler',button,async()=>{try{await api('/admin/scheduler/run-once',{method:'POST',body:'{}'});feedback('Проверки завершены','ok');await loadCurrent()}catch(e){feedback(compactError(e),'bad')}},'Проверка…')}
boot();
</script>
</body>
</html>
"""
