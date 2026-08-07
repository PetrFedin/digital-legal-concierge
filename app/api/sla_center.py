from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.sla_service import (
    CaseSLAError,
    CaseSLAService,
    SLA_ACTION_OVERDUE,
    SLA_FIRST_RESPONSE_OVERDUE,
)
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, decode_access_token, has_role

router = APIRouter(prefix="/admin/sla", tags=["admin", "sla"])

ROUTE_LABELS = {
    "M1": "Ведение дела",
    "M2": "Консультация",
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
    return ROUTE_LABELS.get(str(route or ""), "Юридическое обращение")


def _sla_label(status: str | None) -> str:
    return SLA_LABELS.get(str(status or ""), "Статус SLA уточняется")


def _escalation_label(level: int | None) -> str:
    value = int(level or 0)
    if value <= 0:
        return "Обычный контроль"
    if value == 1:
        return "Требует повышенного внимания"
    if value == 2:
        return "Высокий приоритет"
    return "Критический приоритет"


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
async def list_sla_cases(
    overdue_only: bool = True,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    query = (
        select(Case, Lawyer, User)
        .join(Lawyer, Lawyer.id == Case.assigned_lawyer_id)
        .join(User, User.id == Case.client_id)
        .where(Case.assigned_lawyer_id.is_not(None))
    )
    if overdue_only:
        query = query.where(
            Case.sla_status.in_(
                [SLA_FIRST_RESPONSE_OVERDUE, SLA_ACTION_OVERDUE]
            )
        )
    else:
        query = query.where(Case.sla_status != "NOT_STARTED")
    rows = (
        await db.execute(
            query.order_by(
                Case.escalation_level.desc(),
                Case.sla_due_at.asc(),
                Case.id.asc(),
            ).limit(500)
        )
    ).all()
    now = datetime.now(timezone.utc)
    return [
        {
            "case_id": case.id,
            "case_number": case.case_number,
            "route": case.route,
            "route_label": _route_label(case.route),
            "case_status": case.status,
            "case_status_label": get_client_visible_status(case.status),
            "next_action": case.next_action,
            "client_id": user.id,
            "client_name": user.full_name,
            "client_telegram_id": user.telegram_id,
            "lawyer_id": lawyer.id,
            "lawyer_name": lawyer.full_name,
            "sla_status": case.sla_status,
            "sla_label": _sla_label(case.sla_status),
            "sla_due_at": (
                case.sla_due_at.isoformat() if case.sla_due_at else None
            ),
            "assigned_at": (
                case.assigned_at.isoformat() if case.assigned_at else None
            ),
            "first_lawyer_response_at": (
                case.first_lawyer_response_at.isoformat()
                if case.first_lawyer_response_at
                else None
            ),
            "last_lawyer_activity_at": (
                case.last_lawyer_activity_at.isoformat()
                if case.last_lawyer_activity_at
                else None
            ),
            "escalation_level": int(case.escalation_level or 0),
            "escalation_label": _escalation_label(case.escalation_level),
            "is_currently_overdue": bool(
                case.sla_due_at
                and (
                    case.sla_due_at.replace(tzinfo=timezone.utc)
                    if case.sla_due_at.tzinfo is None
                    else case.sla_due_at.astimezone(timezone.utc)
                )
                <= now
            ),
        }
        for case, lawyer, user in rows
    ]


@router.post("/{case_id}/acknowledge")
async def acknowledge_sla(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    try:
        case = await CaseSLAService(db).acknowledge_overdue(
            case_id=case_id,
            actor_id=actor_id_from_token(actor),
            comment=payload.get("comment") or "",
            expected_sla_status=payload.get("expected_sla_status"),
            expected_escalation_level=payload.get(
                "expected_escalation_level"
            ),
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except CaseSLAError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "case_id": case.id,
        "sla_status": case.sla_status,
        "sla_due_at": case.sla_due_at,
        "escalation_level": case.escalation_level,
    }


@router.post("/run")
async def run_sla_check(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    try:
        result = await CaseSLAService(db).escalate_overdue_cases()
        await db.commit()
        return result
    except CaseSLAError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise


@router.get("/ui", response_class=HTMLResponse)
async def sla_center_ui():
    return HTMLResponse(SLA_CENTER_HTML)


SLA_CENTER_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SLA юридической работы</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--blue2:#eef2ff;--green:#14804a;--green2:#ecfdf3;--red:#b42318;--red2:#fef3f2;--amber:#a15c00;--amber2:#fff7e6;--shadow:0 12px 30px rgba(16,24,40,.07)}
*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:var(--bg);margin:0;color:var(--ink)}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px}.header{max-width:1320px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:16px}.header h1{margin:0 0 4px;font-size:22px}.header p{margin:0;color:#d0d5dd;font-size:13px}.links,.toolbar,.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}a.button,button{border:0;border-radius:10px;padding:9px 12px;color:#fff;background:var(--blue);font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}button:disabled,textarea:disabled{opacity:.55;cursor:wait}.gray{background:#475467}.red{background:var(--red)}.green{background:var(--green)}main{max-width:1320px;margin:auto;padding:22px}.notice{background:var(--amber2);border:1px solid #fedf89;border-radius:13px;padding:13px;margin-bottom:14px;line-height:1.45}.panel,.case{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}.panel{padding:16px}.toolbar{justify-content:space-between;margin-bottom:14px}.filters{display:flex;gap:8px;flex-wrap:wrap}.feedback{min-height:23px;margin:0 0 10px;font-size:13px}.feedback.ok{color:var(--green)}.feedback.bad{color:var(--red)}.feedback.warn{color:var(--amber)}.feedback.muted{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.case{padding:15px}.case-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.case h3{margin:0 0 4px;font-size:18px}.muted{font-size:13px;color:var(--muted)}.badge{display:inline-flex;border-radius:999px;padding:5px 9px;background:var(--red2);color:var(--red);font-size:12px;font-weight:750}.badge.ok{background:var(--green2);color:var(--green)}.meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin:12px 0}.cell{background:#f8fafc;border-radius:10px;padding:10px}.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.next{background:var(--blue2);border:1px solid #c7d2fe;border-radius:11px;padding:11px;line-height:1.4;margin:10px 0}.priority{background:var(--red2);border:1px solid #fecdca;border-radius:10px;padding:10px;margin:8px 0}.actions{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}.plan{display:none;border-top:1px solid var(--line);margin-top:12px;padding-top:12px}.plan.open{display:block}.plan textarea{width:100%;min-height:105px;border:1px solid #d0d5dd;border-radius:10px;padding:10px;resize:vertical;margin:7px 0}.hint{font-size:12px;color:var(--muted);line-height:1.45}.empty,.error,.loading{padding:30px;text-align:center;border:1px dashed var(--line);border-radius:14px;background:#fff;color:var(--muted)}.error{background:var(--red2);color:var(--red)}.empty h3,.error h3{margin-top:0}.rule{font-size:12px;color:var(--muted);margin:8px 0}.rule b{color:var(--ink)}
@media(max-width:900px){.cards{grid-template-columns:1fr}.header,.toolbar{align-items:flex-start;flex-direction:column}.meta{grid-template-columns:1fr 1fr}}
@media(max-width:520px){main{padding:12px}.meta{grid-template-columns:1fr}.links,.filters,.row{width:100%;align-items:stretch;flex-direction:column}a.button,button{width:100%;text-align:center}}
</style>
</head>
<body>
<header><div class="header"><div><h1>⚖ SLA юридической работы</h1><p>Просрочки, ответственность и следующий контрольный шаг — без технических кодов.</p></div><div class="links"><a class="button gray" href="/admin/workdesk/ui">Рабочий стол</a><a class="button gray" href="/operator">Все разделы</a></div></div></header>
<main>
<div class="notice"><b>Правило контроля.</b> Новый срок не скрывает нарушение: исходная просрочка, автор решения и план исправления остаются в аудите. После подтверждения начинается новый контрольный период.</div>
<div id="message" class="feedback muted" role="status" aria-live="polite"></div>
<section class="panel"><div class="toolbar"><div class="filters"><button data-sla-filter onclick="load(true,this)" class="gray">Только просроченные</button><button data-sla-filter onclick="load(false,this)" class="gray">Все активные SLA</button></div><button data-sla-run onclick="runCheck(this)" class="red">Проверить просрочки сейчас</button></div><div id="content"><div class="loading">Загрузка контроля SLA…</div></div></section>
</main>
<script>
let token='',runPending=false,currentOverdueOnly=true,loadController=null;const pendingCases=new Set(),draftPlans=new Map();let rowsById=new Map();
const slaLabels={NOT_STARTED:'SLA не запущен',FIRST_RESPONSE_PENDING:'Ожидается первая реакция',FIRST_RESPONSE_OK:'Первая реакция в срок',FIRST_RESPONSE_OVERDUE:'Просрочена первая реакция',ACTION_PENDING:'Ожидается действие',ACTION_OK:'Действие выполнено в срок',ACTION_OVERDUE:'Действие просрочено'};
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';throw new Error('Сессия истекла или недостаточно прав')}const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка запроса');return d}
function feedback(text,state='muted'){message.textContent=text;message.className='feedback '+state}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function dt(v){return v?new Date(v).toLocaleString('ru-RU'):'—'}
function slaLabel(v){return slaLabels[String(v||'')]||'Статус SLA уточняется'}
function caseControls(id){return Array.from(document.querySelectorAll(`[data-case-id="${id}"]`))}
function rememberDraft(id,value){draftPlans.set(Number(id),value)}
function caseCard(x){const id=Number(x.case_id),overdue=String(x.sla_status||'').includes('OVERDUE'),draft=draftPlans.get(id)||'';return `<article class="case"><div class="case-head"><div><h3>${esc(x.case_number)}</h3><div class="muted">${esc(x.route_label||'Юридическое обращение')} · ${esc(x.case_status_label||'Статус дела уточняется')}</div></div><span class="badge ${overdue?'':'ok'}">${esc(x.sla_label||slaLabel(x.sla_status))}</span></div><div class="meta"><div class="cell"><span>Клиент</span>${esc(x.client_name||'Не указан')}</div><div class="cell"><span>Ответственный юрист</span>${esc(x.lawyer_name||'Не назначен')}</div><div class="cell"><span>Контрольный срок</span>${esc(dt(x.sla_due_at))}</div><div class="cell"><span>Приоритет контроля</span>${esc(x.escalation_label||'Обычный контроль')}</div></div><div class="next"><b>Следующий шаг по делу</b><br>${esc(x.next_action||'Связаться с клиентом и определить ближайшее действие')}</div>${overdue?`<div class="priority"><b>Просрочка зафиксирована.</b> Принятие в работу не удалит её из аудита.</div><div class="actions row"><button data-case-id="${id}" onclick="openPlan(${id})">Принять в работу и установить новый срок</button><a class="button gray" href="/admin/workdesk/cases/${id}/action/sla">Открыть дело</a><a class="button gray" href="/message-center/ui?case_id=${id}">Переписка</a></div><div id="sla_plan_${id}" class="plan"><b>План устранения просрочки</b><textarea id="sla_comment_${id}" oninput="rememberDraft(${id},this.value)" placeholder="Причина просрочки, что делаем сейчас и какой следующий контрольный шаг">${esc(draft)}</textarea><div class="hint">Минимум 5 символов. Опишите конкретный план, который можно проверить по следующему сроку.</div><div class="rule"><b>Ничего не изменится</b>, пока вы не нажмёте «Подтвердить план».</div><div class="row"><button data-case-id="${id}" class="green" onclick="ack(${id},this)">Подтвердить план</button><button class="gray" onclick="closePlan(${id})">Вернуться без сохранения</button></div></div>`:`<div class="actions row"><a class="button" href="/admin/workdesk/cases/${id}/action/sla">Открыть дело</a><a class="button gray" href="/message-center/ui?case_id=${id}">Переписка</a></div>`}</article>`}
function renderRows(rows){rowsById=new Map(rows.map(x=>[Number(x.case_id),x]));content.innerHTML=rows.length?`<div class="cards">${rows.map(caseCard).join('')}</div>`:`<div class="empty"><h3>В выбранной категории дел нет</h3><p>Контроль не требует действия. Можно вернуться к общему рабочему столу или показать все активные SLA.</p><div class="row" style="justify-content:center"><a class="button" href="/admin/workdesk/ui">Рабочий стол</a>${currentOverdueOnly?'<button class="gray" onclick="load(false,this)">Показать все активные SLA</button>':''}</div></div>`}
function openPlan(id){document.querySelectorAll('.plan').forEach(x=>x.classList.remove('open'));const form=document.getElementById('sla_plan_'+id);if(!form){feedback('Карточка уже изменилась. Обновите список.','bad');return}form.classList.add('open');document.getElementById('sla_comment_'+id)?.focus()}
function closePlan(id){document.getElementById('sla_plan_'+id)?.classList.remove('open')}
async function withRunAction(button,work){if(runPending)return;runPending=true;const controls=Array.from(document.querySelectorAll('[data-sla-run]'));const labels=new Map(controls.map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Проверка…';try{return await work()}finally{runPending=false;controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}
async function withCaseAction(id,button,work){if(pendingCases.has(id))return;pendingCases.add(id);const controls=caseControls(id);const labels=new Map(controls.filter(x=>x.tagName==='BUTTON').map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Сохранение…';try{return await work()}finally{pendingCases.delete(id);controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();const roles=s.roles||[s.role];if(!roles.includes('admin')){location.href='/operator';return}token=s.api_token||'';const result=await load(true);if(!result.ok&&!result.aborted)feedback(`Контроль SLA не загружен: ${result.error}`,'bad')}
async function load(overdue,button=null,reportError=true){if(loadController)loadController.abort();const controller=new AbortController();loadController=controller;const controls=Array.from(document.querySelectorAll('[data-sla-filter]'));const labels=new Map(controls.map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Загрузка…';content.innerHTML='<div class="loading">Обновление контроля SLA…</div>';try{const rows=await api('/admin/sla?overdue_only='+String(overdue),{signal:controller.signal});if(loadController!==controller)return {ok:false,aborted:true};currentOverdueOnly=overdue;renderRows(rows);return {ok:true}}catch(e){if(e.name==='AbortError')return {ok:false,aborted:true};content.innerHTML=`<div class="error"><h3>Список SLA не загружен</h3><p>${esc(e.message)}</p><div class="row" style="justify-content:center"><button onclick="load(currentOverdueOnly)">Повторить</button><a class="button gray" href="/admin/workdesk/ui">Рабочий стол</a></div></div>`;if(reportError)feedback(`Список SLA не загружен: ${e.message}`,'bad');return {ok:false,error:e.message}}finally{if(loadController===controller){loadController=null;controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}}
async function refreshAfter(success){const result=await load(currentOverdueOnly,null,false);if(result.ok)feedback(success,'ok');else if(!result.aborted)feedback(`${success} Изменение сохранено, но список не обновился: ${result.error}. Нажмите «Повторить».`,'warn')}
async function runCheck(button){if(!confirm('Запустить проверку просроченных SLA сейчас? Новые нарушения получат следующий приоритет контроля, запись в аудите и уведомления.'))return;return withRunAction(button,async()=>{let result;try{result=await api('/admin/sla/run',{method:'POST',body:'{}'})}catch(e){feedback(`Проверка SLA не выполнена: ${e.message}`,'bad');return}await refreshAfter(`Проверка завершена. Проверено дел: ${result.examined}. Новых эскалаций: ${result.escalated_count}.`)})}
async function ack(id,button){const item=rowsById.get(Number(id));if(!item){feedback('Данные дела изменились. Обновите список.','bad');return}const expectedStatus=String(item.sla_status||''),expectedLevel=Number(item.escalation_level),comment=(document.getElementById('sla_comment_'+id)?.value||'').trim();if(!expectedStatus||!Number.isInteger(expectedLevel)){feedback('Контрольный снимок SLA устарел. Обновите список.','bad');return}if(comment.length<5){feedback('Опишите причину и план — минимум 5 символов. Черновик сохранён.','bad');return}if(!confirm(`Подтвердить план по делу ${item.case_number} и установить новый контрольный срок? Исходная просрочка останется в аудите.`))return;return withCaseAction(id,button,async()=>{let result;try{result=await api('/admin/sla/'+id+'/acknowledge',{method:'POST',body:JSON.stringify({comment,expected_sla_status:expectedStatus,expected_escalation_level:expectedLevel})})}catch(e){feedback(`План по делу ${item.case_number} не сохранён: ${e.message}. Черновик остаётся на экране.`,'bad');return}draftPlans.delete(Number(id));await refreshAfter(`План по делу ${item.case_number} сохранён. Статус: ${slaLabel(result.sla_status)}. Новый срок: ${dt(result.sla_due_at)}.`)})}
boot();
</script>
</body>
</html>
"""
