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
    result = await CaseSLAService(db).escalate_overdue_cases()
    await db.commit()
    return result


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
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between}main{max-width:1500px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}.notice{background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:12px;margin-bottom:16px}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}button{border:0;border-radius:9px;padding:8px 11px;color:#fff;background:#2563eb;font-weight:700;cursor:pointer}.red{background:#b91c1c}.gray{background:#4b5563}.muted{font-size:13px;color:#6b7280}.badge{display:inline-block;border-radius:999px;padding:4px 8px;background:#fee2e2;color:#991b1b;font-size:12px;font-weight:700}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}@media(max-width:900px){table{display:block;overflow-x:auto;font-size:12px}}
</style>
</head>
<body>
<header><b>⚖ SLA юридической работы</b><a href="/admin-ui" style="color:white">Админка</a></header>
<main><div class="notice"><b>Правило.</b> Продление срока не скрывает просрочку: исходное нарушение и автор решения сохраняются в аудите. После продления начинается новый контрольный период.</div><div class="card"><div class="toolbar"><button onclick="runCheck()" class="red">Запустить проверку сейчас</button><button onclick="load(true)" class="gray">Только просроченные</button><button onclick="load(false)" class="gray">Все активные SLA</button></div><div id="content">Загрузка…</div></div><div id="message" class="muted"></div></main>
<script>
let token='';
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function dt(v){return v?new Date(v).toLocaleString('ru-RU'):'—'}
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;await load(true)}
async function load(overdue){const rows=await api('/admin/sla?overdue_only='+String(overdue));content.innerHTML=rows.length?`<table><tr><th>Дело</th><th>Клиент</th><th>Юрист</th><th>SLA</th><th>Действие</th></tr>${rows.map(x=>`<tr><td><b>${esc(x.case_number)}</b><br>${esc(x.route||'—')} / ${esc(x.case_status)}<br><span class="muted">${esc(x.next_action||'')}</span></td><td>${esc(x.client_name)}<br><span class="muted">TG ${esc(x.client_telegram_id)}</span></td><td>${esc(x.lawyer_name)}<br><span class="muted">назначен ${esc(dt(x.assigned_at))}<br>первая реакция ${esc(dt(x.first_lawyer_response_at))}<br>последняя активность ${esc(dt(x.last_lawyer_activity_at))}</span></td><td><span class="badge">${esc(x.sla_status)}</span><br>срок ${esc(dt(x.sla_due_at))}<br><span class="muted">уровень ${esc(x.escalation_level)}</span></td><td>${String(x.sla_status).includes('OVERDUE')?`<button onclick="ack(${x.case_id})">Принять в работу и продлить</button>`:'—'}</td></tr>`).join('')}</table>`:'Дел в выбранной категории нет.'}
async function runCheck(){try{const r=await api('/admin/sla/run',{method:'POST'});message.textContent='Эскалировано: '+r.escalated_count;await load(true)}catch(e){message.textContent=e.message}}
async function ack(id){const comment=prompt('Укажите причину просрочки и план исправления:');if(!comment)return;try{await api('/admin/sla/'+id+'/acknowledge',{method:'POST',body:JSON.stringify({comment})});message.textContent='Новый контрольный срок установлен';await load(true)}catch(e){message.textContent=e.message}}
boot();
</script>
</body>
</html>
"""
