from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.message import Message
from app.models.payment import Payment
from app.models.task import Task
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_OPERATOR,
    ROLE_SUPERADMIN,
    decode_access_token,
    normalize_roles,
)

router = APIRouter(prefix="/task-center", tags=["task-center"])

VALID_STATUSES = {"OPEN", "IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED"}
VALID_PRIORITIES = {"LOW", "NORMAL", "HIGH", "CRITICAL"}


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _actor(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
) -> tuple[dict, AdminUser | None, Lawyer | None]:
    payload = decode_access_token(_token(request, header_token))
    if not payload:
        raise HTTPException(status_code=401, detail="Требуется вход")
    roles = set(normalize_roles(payload.get("roles")))
    if not roles.intersection({ROLE_ADMIN, ROLE_SUPERADMIN, ROLE_LAWYER, ROLE_OPERATOR}):
        raise HTTPException(status_code=403, detail="Нет доступа к задачам")

    user = None
    lawyer = None
    uid = int(payload.get("uid") or 0)
    if uid:
        user = (
            await db.execute(select(AdminUser).where(AdminUser.id == uid, AdminUser.is_active.is_(True)))
        ).scalars().first()
        if not user:
            raise HTTPException(status_code=401, detail="Учетная запись отключена")
        lawyer = (
            await db.execute(select(Lawyer).where(Lawyer.admin_user_id == user.id))
        ).scalars().first()
    return payload, user, lawyer


def _can_manage_all(payload: dict) -> bool:
    roles = set(normalize_roles(payload.get("roles")))
    return bool(roles.intersection({ROLE_ADMIN, ROLE_SUPERADMIN, ROLE_OPERATOR}))


def _parse_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Некорректная дата срока") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


async def _task_or_404(db: AsyncSession, task_id: int) -> Task:
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


def _assert_task_access(task: Task, payload: dict, lawyer: Lawyer | None) -> None:
    if _can_manage_all(payload):
        return
    if not lawyer or task.assigned_lawyer_id != lawyer.id:
        raise HTTPException(status_code=403, detail="Задача не назначена этому юристу")


def _snapshot(task: Task) -> dict:
    return {
        "id": task.id,
        "case_id": task.case_id,
        "assigned_lawyer_id": task.assigned_lawyer_id,
        "created_by_admin_user_id": task.created_by_admin_user_id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


async def _audit(
    db: AsyncSession,
    payload: dict,
    action: str,
    task: Task,
    old_value: dict | None,
    new_value: dict | None,
    comment: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_type="admin_user",
            actor_id=int(payload.get("uid") or 0) or None,
            action=action,
            entity_type="task",
            entity_id=task.id,
            old_value=old_value,
            new_value=new_value,
            comment=comment,
        )
    )


@router.get("/status")
async def task_center_status(db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)

    async def count(statement) -> int:
        return int((await db.execute(statement)).scalar_one() or 0)

    data = {
        "new_cases": await count(select(func.count(Case.id)).where(Case.status == "NEW")),
        "cases_without_lawyer": await count(
            select(func.count(Case.id)).where(
                Case.assigned_lawyer_id.is_(None),
                Case.is_archived.is_(False),
                Case.status.notin_(["M1_CLOSED", "M2_CLOSED", "ARCHIVED"]),
            )
        ),
        "documents_on_review": await count(
            select(func.count(Document.id)).where(Document.status.in_(["UPLOADED", "PENDING", "ON_REVIEW"]))
        ),
        "payments_waiting": await count(
            select(func.count(Payment.id)).where(Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"]))
        ),
        "consultations_booked": await count(
            select(func.count(Consultation.id)).where(Consultation.status == "BOOKED")
        ),
        "unread_messages": await count(
            select(func.count(Message.id)).where(or_(Message.is_read.is_(False), Message.is_read.is_(None)))
        ),
        "tasks_open": await count(
            select(func.count(Task.id)).where(Task.status.in_(["OPEN", "IN_PROGRESS", "BLOCKED"]))
        ),
        "tasks_overdue": await count(
            select(func.count(Task.id)).where(
                Task.status.in_(["OPEN", "IN_PROGRESS", "BLOCKED"]),
                Task.due_at.is_not(None),
                Task.due_at < now,
            )
        ),
        "tasks_critical": await count(
            select(func.count(Task.id)).where(
                Task.status.in_(["OPEN", "IN_PROGRESS", "BLOCKED"]),
                Task.priority == "CRITICAL",
            )
        ),
    }
    risks = [key for key in ("tasks_overdue", "tasks_critical", "cases_without_lawyer", "documents_on_review", "unread_messages") if data[key]]
    data["ok"] = not bool(risks)
    data["main_risk"] = (
        "Требуют внимания: " + ", ".join(risks)
        if risks
        else "Критичных операционных задач не найдено"
    )
    return data


@router.get("/tasks")
async def list_tasks(
    request: Request,
    status: str | None = None,
    priority: str | None = None,
    lawyer_id: int | None = None,
    case_id: int | None = None,
    overdue: bool = False,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    payload, _, lawyer = await _actor(request, db, x_admin_token)
    query = select(Task)
    conditions = []
    if not _can_manage_all(payload):
        if not lawyer:
            return []
        conditions.append(Task.assigned_lawyer_id == lawyer.id)
    elif lawyer_id is not None:
        conditions.append(Task.assigned_lawyer_id == lawyer_id)
    if status:
        if status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail="Недопустимый статус")
        conditions.append(Task.status == status)
    if priority:
        if priority not in VALID_PRIORITIES:
            raise HTTPException(status_code=400, detail="Недопустимый приоритет")
        conditions.append(Task.priority == priority)
    if case_id is not None:
        conditions.append(Task.case_id == case_id)
    if overdue:
        conditions.extend(
            [
                Task.status.in_(["OPEN", "IN_PROGRESS", "BLOCKED"]),
                Task.due_at.is_not(None),
                Task.due_at < datetime.now(timezone.utc),
            ]
        )
    if conditions:
        query = query.where(and_(*conditions))
    rows = (
        await db.execute(
            query.order_by(Task.completed_at.is_not(None), Task.due_at.asc(), Task.priority.desc(), Task.id.desc()).limit(min(max(limit, 1), 500))
        )
    ).scalars().all()
    return [_snapshot(task) for task in rows]


@router.post("/tasks")
async def create_task(
    payload_data: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    payload, user, lawyer = await _actor(request, db, x_admin_token)
    title = str(payload_data.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Укажите название задачи")

    priority = str(payload_data.get("priority") or "NORMAL").upper()
    if priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=400, detail="Недопустимый приоритет")

    assigned_lawyer_id = payload_data.get("assigned_lawyer_id")
    if not _can_manage_all(payload):
        if not lawyer:
            raise HTTPException(status_code=409, detail="Не создан профиль юриста")
        assigned_lawyer_id = lawyer.id
    elif assigned_lawyer_id not in (None, ""):
        assigned_lawyer_id = int(assigned_lawyer_id)
        target = (
            await db.execute(select(Lawyer).where(Lawyer.id == assigned_lawyer_id, Lawyer.is_active.is_(True)))
        ).scalars().first()
        if not target:
            raise HTTPException(status_code=404, detail="Активный юрист не найден")
    else:
        assigned_lawyer_id = None

    case_id = payload_data.get("case_id")
    if case_id not in (None, ""):
        case_id = int(case_id)
        case = (await db.execute(select(Case).where(Case.id == case_id))).scalars().first()
        if not case:
            raise HTTPException(status_code=404, detail="Дело не найдено")
    else:
        case_id = None

    task = Task(
        case_id=case_id,
        assigned_lawyer_id=assigned_lawyer_id,
        created_by_admin_user_id=user.id if user else None,
        title=title,
        description=str(payload_data.get("description") or "").strip() or None,
        status="OPEN",
        priority=priority,
        due_at=_parse_dt(payload_data.get("due_at")),
    )
    db.add(task)
    await db.flush()
    await _audit(db, payload, "task.created", task, None, _snapshot(task))
    await db.commit()
    await db.refresh(task)
    return {"ok": True, "task": _snapshot(task)}


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: int,
    payload_data: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    payload, _, lawyer = await _actor(request, db, x_admin_token)
    task = await _task_or_404(db, task_id)
    _assert_task_access(task, payload, lawyer)
    old = _snapshot(task)

    if "title" in payload_data:
        title = str(payload_data.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Название не может быть пустым")
        task.title = title
    if "description" in payload_data:
        task.description = str(payload_data.get("description") or "").strip() or None
    if "priority" in payload_data:
        priority = str(payload_data.get("priority") or "").upper()
        if priority not in VALID_PRIORITIES:
            raise HTTPException(status_code=400, detail="Недопустимый приоритет")
        task.priority = priority
    if "due_at" in payload_data:
        task.due_at = _parse_dt(payload_data.get("due_at"))
    if "assigned_lawyer_id" in payload_data:
        if not _can_manage_all(payload):
            raise HTTPException(status_code=403, detail="Только администратор может переназначать задачу")
        value = payload_data.get("assigned_lawyer_id")
        if value in (None, ""):
            task.assigned_lawyer_id = None
        else:
            target_id = int(value)
            target = (
                await db.execute(select(Lawyer).where(Lawyer.id == target_id, Lawyer.is_active.is_(True)))
            ).scalars().first()
            if not target:
                raise HTTPException(status_code=404, detail="Активный юрист не найден")
            task.assigned_lawyer_id = target_id
    if "status" in payload_data:
        status = str(payload_data.get("status") or "").upper()
        if status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail="Недопустимый статус")
        task.status = status
        task.completed_at = datetime.now(timezone.utc) if status == "DONE" else None

    await _audit(db, payload, "task.updated", task, old, _snapshot(task), str(payload_data.get("comment") or "") or None)
    await db.commit()
    await db.refresh(task)
    return {"ok": True, "task": _snapshot(task)}


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    payload, _, lawyer = await _actor(request, db, x_admin_token)
    task = await _task_or_404(db, task_id)
    _assert_task_access(task, payload, lawyer)
    old = _snapshot(task)
    task.status = "DONE"
    task.completed_at = datetime.now(timezone.utc)
    await _audit(db, payload, "task.completed", task, old, _snapshot(task))
    await db.commit()
    return {"ok": True, "task": _snapshot(task)}


@router.get("/ui", response_class=HTMLResponse)
async def task_center_ui():
    return HTMLResponse(TASK_CENTER_HTML)


TASK_CENTER_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Задачи</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between}main{max-width:1200px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}input,select,textarea{width:100%;box-sizing:border-box;padding:10px;border:1px solid #d1d5db;border-radius:9px}button{border:0;border-radius:9px;padding:10px 13px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer}.task{border-bottom:1px solid #e5e7eb;padding:12px 0}.muted{color:#6b7280;font-size:13px}.badge{display:inline-block;background:#e5e7eb;border-radius:999px;padding:4px 8px;margin-right:5px;font-size:12px}.danger{background:#fee2e2}.warn{background:#fef3c7}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style></head>
<body><header><b>⚖ Центр задач</b><a href="/admin-ui" style="color:white">Админка</a></header><main>
<div class="card"><h2>Новая задача</h2><div class="grid"><input id="title" placeholder="Название"><input id="case_id" type="number" placeholder="ID дела"><input id="lawyer_id" type="number" placeholder="ID юриста"><select id="priority"><option>NORMAL</option><option>HIGH</option><option>CRITICAL</option><option>LOW</option></select><input id="due_at" type="datetime-local"><textarea id="description" placeholder="Описание"></textarea></div><p><button onclick="createTask()">Создать</button></p><div id="message" class="muted"></div></div>
<div class="card"><h2>Задачи</h2><p><select id="filter" onchange="loadTasks()"><option value="">Все</option><option>OPEN</option><option>IN_PROGRESS</option><option>BLOCKED</option><option>DONE</option><option>CANCELLED</option></select></p><div id="tasks">Загрузка…</div></div></main>
<script>let token='';async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;await loadTasks()}async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}async function loadTasks(){const q=filter.value?'?status='+filter.value:'';const rows=await api('/task-center/tasks'+q);tasks.innerHTML=rows.length?rows.map(t=>`<div class="task ${t.priority==='CRITICAL'?'danger':t.priority==='HIGH'?'warn':''}"><b>${esc(t.title)}</b><br><span class="badge">${esc(t.status)}</span><span class="badge">${esc(t.priority)}</span><span class="muted">Дело: ${esc(t.case_id||'—')} · Юрист: ${esc(t.assigned_lawyer_id||'—')} · Срок: ${esc(t.due_at||'—')}</span><p>${esc(t.description||'')}</p>${t.status!=='DONE'?`<button onclick="completeTask(${t.id})">Выполнено</button>`:''}</div>`).join(''):'Задач нет'}async function createTask(){try{await api('/task-center/tasks',{method:'POST',body:JSON.stringify({title:title.value,case_id:case_id.value||null,assigned_lawyer_id:lawyer_id.value||null,priority:priority.value,due_at:due_at.value||null,description:description.value})});message.textContent='Задача создана';title.value='';description.value='';await loadTasks()}catch(e){message.textContent=e.message}}async function completeTask(id){await api('/task-center/tasks/'+id+'/complete',{method:'POST'});await loadTasks()}boot()</script></body></html>
"""
