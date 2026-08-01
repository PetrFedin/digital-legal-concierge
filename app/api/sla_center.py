from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
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
            "case_status": case.status,
            "next_action": case.next_action,
            "client_id": user.id,
            "client_name": user.full_name,
            "client_telegram_id": user.telegram_id,
            "lawyer_id": lawyer.id,
            "lawyer_name": lawyer.full_name,
            "sla_status": case.sla_status,
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
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between}main{max-width:1500px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}.notice{background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:12px;margin-bottom:16px}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}button{border:0;border-radius:9px;padding:8px 11px;color:#fff;background:#2563eb;font-weight:700;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.red{background:#b91c1c}.gray{background:#4b5563}.muted{font-size:13px;color:#6b7280}.ok{color:#15803d}.bad{color:#b91c1c}.warn{color:#a16207}.badge{display:inline-block;border-radius:999px;padding:4px 8px;background:#fee2e2;color:#991b1b;font-size:12px;font-weight:700}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}@media(max-width:900px){table{display:block;overflow-x:auto;font-size:12px}}
</style>
</head>
<body>
<header><b>⚖ SLA юридической работы</b><a href="/admin-ui" style="color:white">Админка</a></header>
<main><div class="notice"><b>Правило.</b> Продление срока не скрывает просрочку: исходное нарушение и автор решения сохраняются в аудите. После продления начинается новый контрольный период.</div><div class="card"><div class="toolbar"><button data-sla-run onclick="runCheck(this)" class="red">Запустить проверку сейчас</button><button data-sla-filter onclick="load(true,this)" class="gray">Только просроченные</button><button data-sla-filter onclick="load(false,this)" class="gray">Все активные SLA</button></div><div id="content">Загрузка…</div></div><div id="message" class="muted" role="status" aria-live="polite"></div></main>
<script>
let token='';let runPending=false;let currentOverdueOnly=true;let loadController=null;const pendingCases=new Set();
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
function feedback(text,state='muted'){message.textContent=text;message.className=state}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function dt(v){return v?new Date(v).toLocaleString('ru-RU'):'—'}
function caseControls(id){return Array.from(document.querySelectorAll(`[data-case-id="${id}"]`))}
async function withRunAction(button,work){if(runPending)return;runPending=true;const controls=Array.from(document.querySelectorAll('[data-sla-run]'));const labels=new Map(controls.map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Проверка…';try{return await work()}finally{runPending=false;controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}
async function withCaseAction(id,button,work){if(pendingCases.has(id))return;pendingCases.add(id);const controls=caseControls(id);const labels=new Map(controls.map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Сохранение…';try{return await work()}finally{pendingCases.delete(id);controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;const result=await load(true);if(!result.ok&&!result.aborted)feedback(`SLA Center не загружен: ${result.error}`,'bad')}
async function load(overdue,button=null,reportError=true){if(loadController)loadController.abort();const controller=new AbortController();loadController=controller;const controls=Array.from(document.querySelectorAll('[data-sla-filter]'));const labels=new Map(controls.map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Загрузка…';try{const rows=await api('/admin/sla?overdue_only='+String(overdue),{signal:controller.signal});if(loadController!==controller)return {ok:false,aborted:true};currentOverdueOnly=overdue;content.innerHTML=rows.length?`<table><tr><th>Дело</th><th>Клиент</th><th>Юрист</th><th>SLA</th><th>Действие</th></tr>${rows.map(x=>`<tr><td><b>${esc(x.case_number)}</b><br>${esc(x.route||'—')} / ${esc(x.case_status)}<br><span class="muted">${esc(x.next_action||'')}</span></td><td>${esc(x.client_name)}<br><span class="muted">TG ${esc(x.client_telegram_id)}</span></td><td>${esc(x.lawyer_name)}<br><span class="muted">назначен ${esc(dt(x.assigned_at))}<br>первая реакция ${esc(dt(x.first_lawyer_response_at))}<br>последняя активность ${esc(dt(x.last_lawyer_activity_at))}</span></td><td><span class="badge">${esc(x.sla_status)}</span><br>срок ${esc(dt(x.sla_due_at))}<br><span class="muted">уровень ${esc(x.escalation_level)}</span></td><td>${String(x.sla_status).includes('OVERDUE')?`<button data-case-id="${x.case_id}" onclick="ack(${x.case_id},'${esc(x.sla_status)}',${x.escalation_level},this)">Принять в работу и продлить</button>`:'—'}</td></tr>`).join('')}</table>`:'Дел в выбранной категории нет.';return {ok:true}}catch(e){if(e.name==='AbortError')return {ok:false,aborted:true};if(reportError)feedback(`Список SLA не загружен: ${e.message}`,'bad');return {ok:false,error:e.message}}finally{if(loadController===controller){loadController=null;controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}}
async function refreshAfter(success){const result=await load(currentOverdueOnly,null,false);if(result.ok)feedback(success,'ok');else if(!result.aborted)feedback(`${success}. Изменение сохранено, но список не обновился: ${result.error}`,'warn')}
async function runCheck(button){if(!confirm('Запустить немедленную проверку всех просроченных SLA? Новые нарушения получат следующий уровень эскалации, запись в аудите и уведомления.'))return;return withRunAction(button,async()=>{try{const result=await api('/admin/sla/run',{method:'POST',body:'{}'});await refreshAfter(`Проверено дел: ${result.examined}. Эскалировано: ${result.escalated_count}`)}catch(e){feedback(`Проверка SLA не выполнена: ${e.message}`,'bad')}})}
async function ack(id,expectedStatus,expectedLevel,button){const comment=prompt('Укажите причину просрочки и конкретный план исправления:');if(!comment)return;if(comment.trim().length<5){feedback('Комментарий должен содержать не менее 5 символов','bad');return}if(!confirm(`Подтвердить просрочку ${expectedStatus}, уровень ${expectedLevel}, по делу #${id} и установить новый контрольный срок? Исходное нарушение останется в аудите.`))return;return withCaseAction(id,button,async()=>{try{const result=await api('/admin/sla/'+id+'/acknowledge',{method:'POST',body:JSON.stringify({comment,expected_sla_status:expectedStatus,expected_escalation_level:expectedLevel})});await refreshAfter(`SLA дела #${result.case_id} принят в работу: ${result.sla_status}, новый срок ${dt(result.sla_due_at)}`)}catch(e){feedback(`SLA дела #${id} не подтверждён: ${e.message}`,'bad')}})}
boot();
</script>
</body>
</html>
"""
