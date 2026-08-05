from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import actor_id_from_token, require_admin
from app.db.session import get_db
from app.domain.cases.case_service import CaseService
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.case_transition_policy import (
    CaseTransitionError,
    TERMINAL_STATUSES,
    allowed_next_statuses,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case

router = APIRouter(tags=["admin-workdesk"])


def _status_option(status: CaseStatus) -> dict[str, object]:
    return {
        "status": status.value,
        "label": get_client_visible_status(status),
        "next_action": CaseService.get_next_action(status),
        "route": (
            "M1"
            if status.value.startswith("M1_")
            else "M2"
            if status.value.startswith("M2_")
            else None
        ),
        "terminal": status in TERMINAL_STATUSES,
        "danger": status == CaseStatus.ERROR,
    }


def _ordered_options(status: str | CaseStatus) -> list[dict[str, object]]:
    options = sorted(
        allowed_next_statuses(status),
        key=lambda item: (
            item == CaseStatus.ERROR,
            item in TERMINAL_STATUSES,
            item.value,
        ),
    )
    return [_status_option(item) for item in options]


@router.get("/admin/workdesk/cases/{case_id}/status-options")
async def workdesk_status_options(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    case = await db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Дело не найдено")
    return {
        "case_id": case.id,
        "current_status": str(case.status),
        "current_label": get_client_visible_status(case.status),
        "updated_at": case.updated_at.isoformat(),
        "options": _ordered_options(case.status),
        "correction_workspace": "/admin-ui",
    }


@router.post("/admin/workdesk/cases/{case_id}/advance")
async def workdesk_advance_case(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    target = str(payload.get("status") or "").strip()
    comment = str(payload.get("comment") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="Выберите следующий этап")
    if len(comment) < 5:
        raise HTTPException(
            status_code=400,
            detail="Укажите основание изменения — не менее 5 символов",
        )

    try:
        case = (
            await db.execute(
                select(Case).where(Case.id == case_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=404, detail="Дело не найдено")

        expected_status = payload.get("expected_status")
        expected_updated_at = payload.get("expected_updated_at")
        if expected_status is not None and str(case.status) != str(expected_status):
            raise HTTPException(
                status_code=409,
                detail="Этап дела уже изменился. Обновите карточку перед повтором",
            )
        if (
            expected_updated_at is not None
            and case.updated_at.isoformat() != str(expected_updated_at)
        ):
            raise HTTPException(
                status_code=409,
                detail="Карточка дела обновилась. Проверьте новые данные",
            )

        try:
            target_status = CaseStatus(target)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail="Неизвестный этап дела",
            ) from error

        if target_status not in allowed_next_statuses(case.status):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Этот переход больше недоступен из текущего этапа. "
                    "Обновите карточку"
                ),
            )

        await CaseService(db).change_status(
            case=case,
            next_status=target_status,
            actor_type="admin",
            actor_id=actor_id_from_token(actor),
            comment=comment,
            force=False,
        )
        await db.commit()
        await db.refresh(case)
        return {
            "ok": True,
            "case_id": case.id,
            "status": str(case.status),
            "status_label": get_client_visible_status(case.status),
            "next_action": case.next_action,
            "updated_at": case.updated_at.isoformat(),
        }
    except HTTPException:
        await db.rollback()
        raise
    except CaseTransitionError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise


@router.get("/admin/workdesk/ui", response_class=HTMLResponse)
async def workdesk_ui():
    return HTMLResponse(WORKDESK_HTML)


WORKDESK_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Digital Legal Concierge — единый рабочий стол</title>
<style>
:root{--bg:#f3f5f9;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--primary:#3157d5;--primary-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;--amber:#a15c00;--amber-soft:#fff7e6;--red:#b42318;--red-soft:#fef3f2;--shadow:0 12px 34px rgba(16,24,40,.08)}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:17px 22px;display:flex;align-items:center;justify-content:space-between;gap:14px}header h1{font-size:20px;margin:0 0 3px}header p{margin:0;color:#d0d5dd;font-size:13px}.header-links{display:flex;gap:8px;flex-wrap:wrap}.header-links a{color:#fff;text-decoration:none;border:1px solid rgba(255,255,255,.26);padding:8px 10px;border-radius:9px;font-size:13px}.layout{display:grid;grid-template-columns:220px minmax(0,1fr) 390px;min-height:calc(100vh - 73px)}nav,.case-panel{background:var(--surface)}nav{border-right:1px solid var(--line);padding:16px}.case-panel{border-left:1px solid var(--line);padding:18px;overflow:auto}.content{padding:20px;overflow:auto}.nav-title{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:12px 8px 6px}.nav-button{width:100%;border:0;background:transparent;text-align:left;padding:10px;border-radius:10px;font-weight:700;color:var(--ink);cursor:pointer}.nav-button:hover,.nav-button.active{background:var(--primary-soft);color:#2445b5}.toolbar,.row,.list-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.toolbar{justify-content:space-between;margin-bottom:14px}.card,.list-item{background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow);border-radius:15px}.card{padding:16px}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px}.metric{border:1px solid var(--line);background:#fff;border-radius:13px;padding:14px;text-align:left;cursor:pointer}.metric b{display:block;font-size:26px}.metric span,.muted{color:var(--muted);font-size:13px}.metric.warn{background:var(--amber-soft);border-color:#fedf89}.metric.bad{background:var(--red-soft);border-color:#fecdca}.queue-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;margin-top:14px}.queue-card{border:1px solid var(--line);background:#fff;border-radius:13px;padding:14px;text-align:left;cursor:pointer}.queue-card:hover,.metric:hover{border-color:#aab8ee;background:var(--primary-soft)}.queue-card .count{float:right;font-size:22px;font-weight:800}.list-item{padding:14px;margin:10px 0;box-shadow:none}.list-head{justify-content:space-between;align-items:flex-start}.badge{display:inline-flex;padding:4px 8px;border-radius:999px;background:#eef2f6;font-size:12px;font-weight:750}.badge.red{background:var(--red-soft);color:var(--red)}button,.button{border:0;background:var(--primary);color:#fff;padding:9px 12px;border-radius:9px;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}button.secondary,.button.secondary{background:#475467}button.green{background:var(--green)}button.danger{background:var(--red)}button:disabled,input:disabled,select:disabled,textarea:disabled{opacity:.55;cursor:wait}.empty,.loading,.error{padding:26px;text-align:center;border:1px dashed var(--line);border-radius:13px;color:var(--muted)}.error{background:var(--red-soft);color:var(--red)}.action-box{background:var(--primary-soft);border:1px solid #c7d2fe;border-radius:12px;padding:13px;margin:12px 0}.action-box b{display:block;margin-bottom:4px}.data-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.data-cell{background:#f8fafc;border-radius:9px;padding:10px}.data-cell span{display:block;color:var(--muted);font-size:12px;margin-bottom:3px}.section{border-top:1px solid var(--line);margin-top:14px;padding-top:14px}.section h4{margin:0 0 8px}select,textarea{width:100%;padding:9px;border:1px solid #d0d5dd;border-radius:9px;margin:5px 0}textarea{min-height:82px;resize:vertical}.transition-note{font-size:13px;color:var(--muted);background:#f8fafc;padding:10px;border-radius:9px;margin:6px 0}.feedback{min-height:20px;font-size:13px;margin-bottom:8px}.feedback.ok{color:var(--green)}.feedback.bad{color:var(--red)}.feedback.warn{color:var(--amber)}@media(max-width:1180px){.layout{grid-template-columns:205px 1fr}.case-panel{grid-column:1/-1;border-left:0;border-top:1px solid var(--line)}}@media(max-width:760px){header{align-items:flex-start;flex-direction:column}.layout{display:block}nav{display:flex;overflow:auto;gap:6px;border-right:0;padding:10px}.nav-title{display:none}.nav-button{width:auto;white-space:nowrap}.content,.case-panel{padding:13px}.metric-grid,.queue-grid{grid-template-columns:1fr 1fr}}@media(max-width:480px){.metric-grid,.queue-grid,.data-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><div><h1>⚖ Единый рабочий стол</h1><p>Сначала очередь, затем дело, затем одно проверяемое действие.</p></div><div class="header-links"><a href="/message-center/ui">Сообщения</a><a href="/admin-ui">Расширенная панель</a><a href="/operator">Все разделы</a></div></header>
<div class="layout">
<nav>
<div class="nav-title">Работа</div>
<button class="nav-button active" data-view="overview" onclick="showOverview(this)">Обзор</button>
<button class="nav-button" data-view="unassigned" onclick="openQueue('unassigned',this)">Без юриста</button>
<button class="nav-button" data-view="documents" onclick="openQueue('documents',this)">Документы</button>
<button class="nav-button" data-view="consultations" onclick="openQueue('consultations',this)">Консультации</button>
<button class="nav-button" data-view="overdue" onclick="openQueue('overdue',this)">Просрочки</button>
</nav>
<main class="content"><div class="toolbar"><div><b id="title">Обзор</b><div id="freshness" class="muted"></div></div><button class="secondary" onclick="reloadCurrent(this)">Обновить</button></div><div id="feedback" class="feedback" role="status" aria-live="polite"></div><div id="view" class="card"><div class="loading">Загрузка…</div></div></main>
<aside class="case-panel"><h3>Карточка дела</h3><div id="caseView" class="muted">Откройте дело из очереди.</div></aside>
</div>
<script>
let token='',mode='overview',queue='unassigned',selectedCase=null,pending=false;
const view=document.getElementById('view'),caseView=document.getElementById('caseView'),feedback=document.getElementById('feedback'),freshness=document.getElementById('freshness'),title=document.getElementById('title');
const queueTitles={unassigned:'Дела без юриста',documents:'Документы на проверке',consultations:'Консультации сегодня',overdue:'Просроченные действия'};
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}
function say(text,state=''){feedback.textContent=text;feedback.className='feedback '+state}
function fmt(v){if(!v)return '—';try{return new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v))}catch{return v}}
async function api(path,opts={}){if(!token)throw new Error('Персональная сессия не загружена');const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';throw new Error('Сессия истекла')}const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.detail||'Ошибка запроса');return data}
function activeButton(button){document.querySelectorAll('.nav-button').forEach(x=>x.classList.toggle('active',x===button))}
function loading(text='Загрузка…'){view.innerHTML=`<div class="loading">${esc(text)}</div>`}
function errorState(e,retry){view.innerHTML=`<div class="error"><b>Не удалось загрузить данные</b><p>${esc(e.message||e)}</p><button onclick="${retry}">Повторить</button></div>`}
function queueCard(key,label,count,note,bad=false){return `<button class="queue-card" onclick="openQueue('${key}',document.querySelector('[data-view=${key}]'))"><span class="count">${count||0}</span><b>${esc(label)}</b><div class="muted">${esc(note)}</div></button>`}
async function boot(){try{const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('admin'))throw new Error('Требуется роль администратора');token=s.api_token||'';await loadOverview()}catch(e){errorState(e,'boot()')}}
async function showOverview(button){mode='overview';activeButton(button);title.textContent='Обзор';await loadOverview()}
async function loadOverview(){loading();say('');try{const d=await api('/admin/dashboard');freshness.textContent='Обновлено '+fmt(d.generated_at);view.innerHTML=`<div class="metric-grid"><button class="metric" onclick="openQueue('unassigned',document.querySelector('[data-view=unassigned]'))"><b>${d.cases?.active||0}</b><span>активных дел</span></button><button class="metric warn" onclick="openQueue('unassigned',document.querySelector('[data-view=unassigned]'))"><b>${d.queue?.unassigned||0}</b><span>без юриста</span></button><button class="metric ${d.queue?.overdue?'bad':''}" onclick="openQueue('overdue',document.querySelector('[data-view=overdue]'))"><b>${d.queue?.overdue||0}</b><span>SLA-просрочек</span></button><button class="metric" onclick="openQueue('consultations',document.querySelector('[data-view=consultations]'))"><b>${d.consultations?.today||0}</b><span>консультаций сегодня</span></button></div><div class="queue-grid">${queueCard('unassigned','Назначить юриста',d.queue?.unassigned,'Дела без ответственного')}${queueCard('documents','Проверить документы',d.queue?.documents_review,'Файлы ожидают решения')}${queueCard('consultations','Провести консультации',d.queue?.consultations_today,'Записи на текущий день')}${queueCard('overdue','Устранить просрочки',d.queue?.overdue,'Требуется немедленная реакция',true)}</div>`}catch(e){errorState(e,'loadOverview()')}}
async function openQueue(name,button){mode='queue';queue=name;activeButton(button);title.textContent=queueTitles[name];loading();say('');try{const d=await api('/admin/work-queues/'+encodeURIComponent(name));freshness.textContent='Обновлено '+fmt(d.generated_at);const items=(d.items||[]).map(x=>`<article class="list-item"><div class="list-head"><div><b>${esc(x.number)}</b><div class="muted">${esc(x.route_label||x.route||'Юридическое обращение')} · создано ${fmt(x.created_at)}</div></div><span class="badge ${x.sla_status?.includes('OVERDUE')?'red':''}">${esc(x.status_label||x.status)}</span></div><p>${esc(x.next_action||'Определить следующий шаг')}</p><div class="muted">Ответственный: ${esc(x.lawyer_name||'не назначен')} · ${esc(x.sla_label||'SLA не запущен')}</div><div class="row" style="margin-top:10px"><button onclick="openCase(${x.id})">Открыть дело</button><a class="button secondary" href="/message-center/ui?case_id=${x.id}">Переписка</a></div></article>`).join('');view.innerHTML=items||'<div class="empty"><b>Очередь пуста</b><p>На текущий момент действий в этой категории нет.</p></div>'}catch(e){errorState(e,`openQueue('${name}',document.querySelector('[data-view=${name}]'))`)}}
async function openCase(id){selectedCase=id;caseView.innerHTML='<div class="loading">Загрузка карточки…</div>';say('');try{const [d,o]=await Promise.all([api('/admin/case-workspace/'+id),api('/admin/workdesk/cases/'+id+'/status-options')]);const docs=(d.documents||[]).map(x=>`<div class="list-item"><b>${esc(x.title)}</b><div class="muted">${esc(x.status_label||x.status)} · версия ${esc(x.version||1)}</div>${x.lawyer_comment?`<div class="feedback warn">Комментарий: ${esc(x.lawyer_comment)}</div>`:''}</div>`).join('')||'<div class="muted">Документов пока нет.</div>';const options=(o.options||[]).map(x=>`<option value="${esc(x.status)}" data-note="${esc(x.next_action)}" data-danger="${x.danger?'1':'0'}">${esc(x.label)}${x.danger?' — системная ошибка':''}</option>`).join('');const transition=options?`<div class="section"><h4>Следующий этап</h4><select id="nextStatus" onchange="showTransitionNote()"><option value="">Выберите разрешённый переход</option>${options}</select><div id="transitionNote" class="transition-note">Список сформирован из действующей схемы процесса.</div><textarea id="transitionComment" placeholder="Основание изменения — минимум 5 символов"></textarea><button id="advanceButton" data-status="${esc(o.current_status)}" data-updated="${esc(o.updated_at)}" onclick="advanceCase(${id},this)">Перевести на следующий этап</button></div>`:`<div class="section"><h4>Следующий этап</h4><div class="transition-note">Для текущего состояния нет стандартного перехода. Если статус ошибочен, используйте расширенную панель: принудительное исправление требует отдельного комментария и аудита.</div><a class="button secondary" href="/admin-ui">Открыть расширенную панель</a></div>`;caseView.innerHTML=`<h3>${esc(d.case.number)}</h3><span class="badge ${d.case.sla_status?.includes('OVERDUE')?'red':''}">${esc(d.case.status_label||d.case.status)}</span><div class="action-box"><b>Рекомендуемое действие</b>${esc(d.case.next_action||'Проверить карточку и определить этап')}</div><div class="data-grid"><div class="data-cell"><span>Услуга</span>${esc(d.case.route_label||d.case.route||'—')}</div><div class="data-cell"><span>Ответственный</span>${esc(d.case.lawyer_name||'не назначен')}</div><div class="data-cell"><span>SLA</span>${esc(d.case.sla_label||'—')}</div><div class="data-cell"><span>Срок</span>${fmt(d.case.sla_due_at)}</div></div>${d.client?`<div class="section"><h4>Клиент</h4><div>${esc(d.client.name||'Имя не указано')}</div><div class="muted">${d.client.username?'@'+esc(d.client.username):'Telegram username не указан'}</div></div>`:''}<div class="section"><div class="row"><a class="button" href="/message-center/ui?case_id=${id}">Открыть переписку</a><a class="button secondary" href="/document-access/review/ui">Проверка документов</a><a class="button secondary" href="/admin/sla/ui">SLA</a></div></div>${transition}<div class="section"><h4>Документы</h4>${docs}</div>`}catch(e){caseView.innerHTML=`<div class="error">${esc(e.message||e)}<br><button onclick="openCase(${id})">Повторить</button></div>`}}
function showTransitionNote(){const s=document.getElementById('nextStatus'),n=document.getElementById('transitionNote'),b=document.getElementById('advanceButton'),o=s?.selectedOptions?.[0];if(!o||!o.value){n.textContent='Список сформирован из действующей схемы процесса.';b.className='';return}n.textContent='После перехода: '+(o.dataset.note||'будет рассчитано следующее действие');b.className=o.dataset.danger==='1'?'danger':''}
async function advanceCase(id,button){if(pending)return;const status=document.getElementById('nextStatus')?.value||'',comment=(document.getElementById('transitionComment')?.value||'').trim();if(!status){say('Выберите следующий этап','bad');return}if(comment.length<5){say('Укажите основание изменения — не менее 5 символов','bad');return}if(!confirm('Изменить этап дела? Действие будет записано в историю.'))return;pending=true;button.disabled=true;const old=button.textContent;button.textContent='Сохранение…';try{const r=await api('/admin/workdesk/cases/'+id+'/advance',{method:'POST',body:JSON.stringify({status,comment,expected_status:button.dataset.status,expected_updated_at:button.dataset.updated})});say('Этап сохранён: '+r.status_label,'ok');await openCase(id);if(mode==='queue')await openQueue(queue,document.querySelector('[data-view='+queue+']'));else await loadOverview()}catch(e){say('Этап не изменён: '+(e.message||e),'bad');await openCase(id)}finally{pending=false;button.disabled=false;button.textContent=old}}
async function reloadCurrent(button){if(pending)return;button.disabled=true;const old=button.textContent;button.textContent='Обновление…';try{if(mode==='queue')await openQueue(queue,document.querySelector('[data-view='+queue+']'));else await loadOverview();if(selectedCase)await openCase(selectedCase)}finally{button.disabled=false;button.textContent=old}}
boot();
</script>
</body>
</html>
"""
