from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.messages.message_priority import (
    CLIENT_URGENCY_NOTE,
    PRIORITY_CRITICAL,
    PRIORITY_TODAY,
    present_message,
    queue_bucket,
)
from app.domain.messages.message_service import MessageService
from app.domain.notifications.immediate_delivery import deliver_selected_notifications
from app.domain.notifications.notification_engine import NotificationEngine
from app.models.case import Case
from app.models.message import Message
from app.models.user import User
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_OPERATOR,
    ROLE_SUPERADMIN,
    decode_access_token,
    normalize_roles,
)
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(tags=["message-center"])
ALLOWED_ROLES = {ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_OPERATOR, ROLE_LAWYER}
BROAD_ACCESS_ROLES = {ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_OPERATOR}
MAX_REPLY_LENGTH = 3800


class ReplyPayload(BaseModel):
    text: str
    lawyer_id: int | None = None
    expected_last_message_id: int | None = Field(
        description="Последнее сообщение, которое видел сотрудник перед отправкой ответа"
    )


@dataclass(frozen=True)
class StaffScope:
    payload: dict
    roles: frozenset[str]
    lawyer_id: int | None

    def allows_case(self, case: Case) -> bool:
        return self.lawyer_id is None or case.assigned_lawyer_id == self.lawyer_id


def _request_token(request: Request, header_token: str | None = None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def require_staff_scope(
    request: Request,
    db: AsyncSession,
    header_token: str | None = None,
) -> StaffScope:
    token = _request_token(request, header_token)
    payload = decode_access_token(token)
    roles = frozenset(normalize_roles(payload.get("roles") if payload else None))
    if not payload:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if not roles.intersection(ALLOWED_ROLES):
        raise HTTPException(
            status_code=403,
            detail="Недостаточно прав для центра сообщений",
        )
    if roles.intersection(BROAD_ACCESS_ROLES):
        return StaffScope(payload=payload, roles=roles, lawyer_id=None)

    lawyer_actor = await require_lawyer_actor(db, token)
    return StaffScope(
        payload=payload,
        roles=roles,
        lawyer_id=lawyer_actor.lawyer.id,
    )


def _ensure_case_access(scope: StaffScope, case: Case) -> None:
    if not scope.allows_case(case):
        raise HTTPException(
            status_code=403,
            detail="Дело не назначено текущему юристу",
        )


def _iso(value):
    return value.isoformat() if value else None


def _age_minutes(value) -> int | None:
    if not value:
        return None
    now = datetime.now(timezone.utc)
    created = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return max(0, int((now - created).total_seconds() // 60))


async def _deliver_message_notifications(
    db: AsyncSession,
    notification_ids: tuple[int, ...],
) -> dict[str, object]:
    # Compatibility wrapper keeps the endpoint contract stable while both the
    # web cabinet and Telegram bot use one selective outbox delivery service.
    return await deliver_selected_notifications(db, notification_ids)


def _message_payload(message: Message) -> dict[str, object]:
    view = present_message(message.text, sender_type=message.sender_type)
    return {
        "id": message.id,
        "sender_type": message.sender_type,
        "sender_id": message.sender_id,
        "text": message.text,
        "body": view.body,
        "category": view.category,
        "urgency": view.urgency,
        "priority": view.priority,
        "priority_label": view.priority_label,
        "priority_note": CLIENT_URGENCY_NOTE if view.structured else None,
        "is_read": message.is_read,
        "created_at": _iso(message.created_at),
    }


@router.get("/message-center/status")
async def message_center_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    scope = await require_staff_scope(request, db, x_admin_token)
    query = select(Message)
    if scope.lawyer_id is not None:
        query = query.join(Case, Case.id == Message.case_id).where(
            Case.assigned_lawyer_id == scope.lawyer_id
        )
    messages = (
        await db.execute(
            query.order_by(Message.created_at.desc(), Message.id.desc()).limit(1000)
        )
    ).scalars().all()

    by_case: dict[int, list[Message]] = defaultdict(list)
    for message in messages:
        by_case[message.case_id].append(message)

    case_ids = list(by_case)
    if not case_ids:
        return {
            "conversation_count": 0,
            "unread_count": 0,
            "waiting_count": 0,
            "critical_count": 0,
            "today_count": 0,
            "unassigned_count": 0,
            "overdue_count": 0,
            "items": [],
        }

    cases = (
        await db.execute(select(Case).where(Case.id.in_(case_ids)))
    ).scalars().all()
    cases_by_id = {case.id: case for case in cases}
    client_ids = list({case.client_id for case in cases})
    clients = (
        await db.execute(select(User).where(User.id.in_(client_ids)))
    ).scalars().all()
    clients_by_id = {client.id: client for client in clients}

    items: list[dict[str, object]] = []
    total_unread = 0
    waiting_count = 0
    critical_count = 0
    today_count = 0
    unassigned_count = 0
    overdue_count = 0
    for case_id, case_messages_list in by_case.items():
        case = cases_by_id.get(case_id)
        if not case or not scope.allows_case(case):
            continue
        client = clients_by_id.get(case.client_id)
        latest = case_messages_list[0]
        latest_view = present_message(
            latest.text,
            sender_type=latest.sender_type,
        )
        unread_count = sum(
            1
            for message in case_messages_list
            if message.sender_type == "client" and not message.is_read
        )
        waiting_for_reply = latest.sender_type == "client"
        age_minutes = _age_minutes(latest.created_at)
        overdue = bool(
            waiting_for_reply
            and age_minutes is not None
            and age_minutes >= 240
        )
        unassigned = case.assigned_lawyer_id is None
        critical = bool(
            waiting_for_reply and latest_view.priority == PRIORITY_CRITICAL
        )
        today = bool(waiting_for_reply and latest_view.priority == PRIORITY_TODAY)
        bucket = queue_bucket(
            waiting_for_reply=waiting_for_reply,
            overdue=overdue,
            priority=latest_view.priority,
        )
        needs_attention = bool(
            waiting_for_reply and (overdue or critical or today or unassigned)
        )

        total_unread += unread_count
        waiting_count += int(waiting_for_reply)
        critical_count += int(critical)
        today_count += int(today)
        unassigned_count += int(waiting_for_reply and unassigned)
        overdue_count += int(overdue)
        items.append(
            {
                "case_id": case.id,
                "case_number": case.case_number,
                "case_status": case.status,
                "lawyer_id": case.assigned_lawyer_id,
                "unassigned": unassigned,
                "client_name": client.full_name if client else None,
                "client_username": client.telegram_username if client else None,
                "latest_message_id": latest.id,
                "latest_sender_type": latest.sender_type,
                "latest_text": latest.text[:1000],
                "latest_preview": latest_view.body[:1000],
                "latest_created_at": _iso(latest.created_at),
                "age_minutes": age_minutes,
                "unread_count": unread_count,
                "waiting_for_reply": waiting_for_reply,
                "overdue": overdue,
                "critical": critical,
                "today": today,
                "needs_attention": needs_attention,
                "category": latest_view.category,
                "urgency": latest_view.urgency,
                "priority": latest_view.priority,
                "priority_label": latest_view.priority_label,
                "priority_note": (
                    CLIENT_URGENCY_NOTE if latest_view.structured else None
                ),
                "queue_bucket": bucket,
                "message_count": len(case_messages_list),
            }
        )

    groups: dict[int, list[dict[str, object]]] = defaultdict(list)
    for item in items:
        groups[int(item["queue_bucket"])].append(item)
    ordered: list[dict[str, object]] = []
    for bucket in range(5):
        ordered.extend(
            sorted(
                groups.get(bucket, []),
                key=lambda item: (
                    bool(item["unassigned"]),
                    str(item["latest_created_at"] or ""),
                ),
                reverse=True,
            )
        )
    return {
        "conversation_count": len(ordered),
        "unread_count": total_unread,
        "waiting_count": waiting_count,
        "critical_count": critical_count,
        "today_count": today_count,
        "unassigned_count": unassigned_count,
        "overdue_count": overdue_count,
        "priority_note": CLIENT_URGENCY_NOTE,
        "items": ordered,
    }


@router.get("/message-center/cases/{case_id}/messages")
async def case_messages(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    scope = await require_staff_scope(request, db, x_admin_token)
    try:
        case = (
            await db.execute(select(Case).where(Case.id == case_id))
        ).scalars().first()
        if not case:
            raise HTTPException(404, "Дело не найдено")
        _ensure_case_access(scope, case)
        client = (
            await db.execute(select(User).where(User.id == case.client_id))
        ).scalars().first()
        service = MessageService(db)
        messages = await service.list_case_messages(case_id)
        latest_message_id = messages[-1].id if messages else None
        await service.mark_client_messages_read(case_id)
        await db.commit()
        return {
            "case": {
                "id": case.id,
                "number": case.case_number,
                "status": case.status,
                "lawyer_id": case.assigned_lawyer_id,
                "unassigned": case.assigned_lawyer_id is None,
                "client_name": client.full_name if client else None,
                "client_username": client.telegram_username if client else None,
            },
            "latest_message_id": latest_message_id,
            "messages": [_message_payload(message) for message in messages],
        }
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/message-center/cases/{case_id}/reply")
async def reply_to_client(
    case_id: int,
    payload: ReplyPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    scope = await require_staff_scope(request, db, x_admin_token)
    text = payload.text.strip()
    if len(text) < 2:
        raise HTTPException(400, "Введите текст ответа")
    if len(text) > MAX_REPLY_LENGTH:
        raise HTTPException(
            400,
            f"Ответ не должен превышать {MAX_REPLY_LENGTH} символов",
        )

    service = MessageService(db)
    try:
        case = await service.lock_case(case_id)
        _ensure_case_access(scope, case)
        latest_message_id = await service.latest_message_id(case_id)
        if latest_message_id != payload.expected_last_message_id:
            raise HTTPException(
                409,
                "В диалоге появились новые сообщения. Обновите переписку перед отправкой ответа.",
            )

        if scope.lawyer_id is not None:
            lawyer_id = scope.lawyer_id
        else:
            lawyer_id = case.assigned_lawyer_id
            if payload.lawyer_id not in (None, lawyer_id):
                raise HTTPException(
                    409,
                    "Нельзя отправить ответ от имени другого юриста",
                )

        created = await service.create_lawyer_message(
            case=case,
            lawyer_id=lawyer_id,
            text=text,
        )
        await service.mark_client_messages_read(case_id)
        notifications = await NotificationEngine(db).emit(
            event_code="STAFF_MESSAGE_REPLIED",
            case_id=case.id,
            user_id=case.client_id,
            payload={
                "case_number": case.case_number,
                "text": text,
            },
            dedupe_key=f"case:{case.id}:message:{created.id}:staff-reply",
        )
        notification_ids = tuple(
            int(item.id) for item in notifications if item.id is not None
        )
        message_id = int(created.id)
        created_at = _iso(created.created_at)

        # The reply and its outbox record become durable before any Telegram
        # network call. Delivery failures therefore cannot erase legal history.
        await db.commit()
        delivery = await _deliver_message_notifications(db, notification_ids)
        return {
            "ok": True,
            "message_id": message_id,
            "created_at": created_at,
            "case_id": case.id,
            "latest_message_id": message_id,
            "delivery": delivery,
        }
    except HTTPException:
        await db.rollback()
        raise
    except LookupError as error:
        await db.rollback()
        raise HTTPException(404, str(error)) from error
    except Exception:
        await db.rollback()
        raise


@router.post("/message-center/{message_id}/read")
async def mark_message_read(
    message_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    scope = await require_staff_scope(request, db, x_admin_token)
    try:
        message = (
            await db.execute(select(Message).where(Message.id == message_id))
        ).scalars().first()
        if not message:
            raise HTTPException(404, "Сообщение не найдено")
        case = await db.get(Case, message.case_id)
        if not case:
            raise HTTPException(404, "Дело не найдено")
        _ensure_case_access(scope, case)
        message.is_read = True
        await db.commit()
        return {"ok": True, "message_id": message.id, "is_read": True}
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.get("/message-center/ui", response_class=HTMLResponse)
async def message_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await require_staff_scope(request, db, x_admin_token)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return HTMLResponse(MESSAGE_CENTER_HTML)


MESSAGE_CENTER_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Digital Legal Concierge — сообщения</title>
<style>
:root{--blue:#2563eb;--green:#15803d;--red:#b91c1c;--amber:#b45309;--purple:#7e22ce;--line:#e5e7eb;--muted:#6b7280}*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}header{background:#111827;color:#fff;padding:18px 22px;display:flex;justify-content:space-between;gap:16px;align-items:center}header h1{margin:0 0 5px;font-size:22px}header p{margin:0;color:#d1d5db}main{padding:18px;max-width:1380px;margin:auto;display:grid;grid-template-columns:470px 1fr;gap:16px}.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}button,.button{background:var(--blue);color:#fff;border:0;border-radius:10px;padding:10px 13px;text-decoration:none;font-weight:700;cursor:pointer}button:disabled,textarea:disabled,select:disabled{opacity:.55;cursor:wait}textarea,select{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:10px;margin:6px 0}textarea{min-height:130px;resize:vertical}.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.toolbar>*{width:auto}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}.metric{background:#f9fafb;border:1px solid var(--line);border-radius:12px;padding:10px}.metric b{font-size:21px;display:block}.item{border:1px solid var(--line);border-radius:12px;padding:12px;margin:9px 0;cursor:pointer}.item:hover{background:#f9fafb}.item.active{border-color:var(--blue);background:#eff6ff}.item.overdue{border-left:5px solid var(--red)}.item.critical:not(.overdue){border-left:5px solid var(--purple)}.item.today:not(.overdue){border-left:5px solid var(--amber)}.item.waiting:not(.overdue):not(.critical):not(.today){border-left:5px solid var(--amber)}.item.answered{border-left:5px solid var(--green)}.badges{display:flex;gap:6px;flex-wrap:wrap;margin:5px 0}.badge{font-size:12px;font-weight:700;border-radius:999px;padding:3px 8px;background:#eef2ff}.badge.red{background:#fee2e2;color:#991b1b}.badge.amber{background:#fef3c7;color:#92400e}.badge.green{background:#dcfce7;color:#166534}.badge.purple{background:#f3e8ff;color:#6b21a8}.badge.gray{background:#f3f4f6;color:#374151}.msg{padding:11px 13px;border-radius:12px;margin:8px 0;white-space:pre-wrap}.client{background:#eef2ff;margin-right:12%}.lawyer{background:#ecfdf5;margin-left:12%}.message-meta{margin:5px 0 2px}.muted{color:var(--muted);font-size:13px}.explain{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:9px;margin:8px 0;color:#78350f;font-size:12px}.error{color:var(--red)}.ok{color:var(--green)}.warn{color:var(--amber)}.hidden{display:none}.empty{padding:24px;text-align:center;color:var(--muted)}#dialog{max-height:55vh;overflow:auto;padding-right:4px}.counter{text-align:right;color:var(--muted);font-size:12px}@media(max-width:960px){main{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(3,1fr)}.client{margin-right:4%}.lawyer{margin-left:4%}}@media(max-width:560px){.metrics{grid-template-columns:1fr 1fr}}
</style></head><body>
<header><div><h1>💬 Центр сообщений</h1><p id="staff">Защищённый доступ для сотрудников.</p></div><form method="post" action="/logout"><button type="submit">Выйти</button></form></header>
<main><section class="card"><h2>Диалоги</h2><div class="toolbar"><button id="refreshButton" data-inbox-refresh onclick="loadMessages(this)">Обновить</button><select id="filter" onchange="renderInbox()"><option value="all">Все диалоги</option><option value="attention">Требуют внимания</option><option value="critical">Клиент: менее 24 часов</option><option value="today">Клиент: ответ сегодня</option><option value="unassigned">Без юриста</option><option value="waiting">Ожидают ответа</option><option value="overdue">Просрочены 4+ часа</option><option value="unread">Непрочитанные</option><option value="answered">Ответ сохранён</option></select><a class="button" href="/operator">Оператор</a></div><div id="metrics" class="metrics"></div><div id="priorityNote" class="explain">Срочность клиента — сигнал для сортировки, а не подтверждённый SLA. Сначала проверяется фактическая просрочка дела.</div><div id="inbox" class="muted">Загрузка...</div></section>
<section class="card"><h2 id="dialogTitle">Переписка</h2><div id="caseMeta" class="muted"></div><div id="dialog" class="empty">Выберите диалог слева.</div><div id="replyBox" class="hidden"><textarea id="replyText" maxlength="3800" placeholder="Ответ юридической команды клиенту"></textarea><div class="counter"><span id="charCount">0</span>/3800</div><button id="sendButton" onclick="sendReply(this)">Сохранить и отправить клиенту</button> <span id="replyStatus" class="muted" role="status" aria-live="polite"></span></div></section></main>
<script>
let currentCaseId=null,currentLatestMessageId=null,conversations=[],sendPending=false,inboxController=null,dialogController=null;
const requestedCaseId=Number(new URLSearchParams(location.search).get('case_id')||0);
const inbox=document.getElementById('inbox'),metrics=document.getElementById('metrics'),dialog=document.getElementById('dialog'),dialogTitle=document.getElementById('dialogTitle'),caseMeta=document.getElementById('caseMeta'),replyBox=document.getElementById('replyBox'),replyText=document.getElementById('replyText'),replyStatus=document.getElementById('replyStatus'),sendButton=document.getElementById('sendButton');
replyText.addEventListener('input',()=>document.getElementById('charCount').textContent=replyText.value.length);
function headers(){return {'Content-Type':'application/json'}}
function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
function feedback(text,state='muted'){replyStatus.textContent=text;replyStatus.className=state}
function deliveryFeedback(delivery){const status=delivery?.status||'queued';if(status==='delivered')return ['Ответ отправлен и сохранён. Клиент получил его в Telegram.','ok'];if(status==='failed')return ['Ответ сохранён, но Telegram отклонил доставку. Ошибка видна в центре доставки.','warn'];if(status==='already_processing')return ['Ответ сохранён; уведомление уже обрабатывается другим процессом.','warn'];if(status==='not_required')return ['Ответ сохранён. Повторное уведомление не требуется.','ok'];return ['Ответ сохранён и поставлен в очередь повторной Telegram-доставки.','warn']}
function ageLabel(m){if(m==null)return '';if(m<60)return m+' мин';const h=Math.floor(m/60),r=m%60;return h+' ч'+(r?' '+r+' мин':'')}
function renderMetrics(d){metrics.innerHTML=`<div class="metric"><b>${d.conversation_count}</b><span class="muted">диалогов</span></div><div class="metric"><b>${d.waiting_count}</b><span class="muted">ждут ответа</span></div><div class="metric"><b>${d.overdue_count}</b><span class="muted">просрочено</span></div><div class="metric"><b>${d.critical_count}</b><span class="muted">клиент: &lt;24 ч</span></div><div class="metric"><b>${d.today_count}</b><span class="muted">ответ сегодня</span></div><div class="metric"><b>${d.unassigned_count}</b><span class="muted">без юриста</span></div>`}
function filterRow(x,f){return f==='all'||(f==='attention'&&x.needs_attention)||(f==='critical'&&x.critical)||(f==='today'&&x.today)||(f==='unassigned'&&x.unassigned&&x.waiting_for_reply)||(f==='waiting'&&x.waiting_for_reply)||(f==='overdue'&&x.overdue)||(f==='unread'&&x.unread_count>0)||(f==='answered'&&!x.waiting_for_reply)}
function priorityBadge(x){if(!x.waiting_for_reply)return '';if(x.priority==='critical')return `<span class="badge purple" title="${esc(x.priority_note||'')}">${esc(x.priority_label||'Срочно')}</span>`;if(x.priority==='today')return `<span class="badge amber" title="${esc(x.priority_note||'')}">${esc(x.priority_label||'Сегодня')}</span>`;return ''}
function renderInbox(){const f=document.getElementById('filter').value,rows=conversations.filter(x=>filterRow(x,f));if(!rows.length){inbox.innerHTML='<div class="empty">По выбранному фильтру диалогов нет.</div>';return}inbox.innerHTML=rows.map(x=>{const cls=x.overdue?'overdue':(x.priority==='critical'&&x.waiting_for_reply?'critical':(x.priority==='today'&&x.waiting_for_reply?'today':(x.waiting_for_reply?'waiting':'answered'))),status=x.overdue?'<span class="badge red">Фактическая просрочка 4+ часа</span>':(x.waiting_for_reply?'<span class="badge amber">Ждёт ответа</span>':'<span class="badge green">Ответ сохранён</span>'),unread=x.unread_count?`<span class="badge">Новых: ${x.unread_count}</span>`:'',category=x.category?`<span class="badge gray">${esc(x.category)}</span>`:'',unassigned=x.unassigned?'<span class="badge red">Юрист не назначен</span>':'';return `<div class="item ${cls} ${currentCaseId===x.case_id?'active':''}" onclick="openCase(${x.case_id})"><b>${esc(x.case_number)}</b> · ${esc(x.client_name||'Клиент')}<div class="badges">${status}${priorityBadge(x)}${unassigned}${unread}${category}</div><div>${esc(x.latest_preview||x.latest_text)}</div><div class="muted">${x.latest_sender_type==='client'?'Клиент':'Команда'} · ${ageLabel(x.age_minutes)} · сообщений: ${x.message_count}</div></div>`}).join('')}
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{...headers(),...(opts.headers||{})}});if(r.status===401){location.href='/login';throw new Error('Сессия истекла')}const d=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(d.detail||'Ошибка');e.status=r.status;throw e}return d}
async function boot(){try{const s=await api('/auth/session');document.getElementById('staff').textContent=`${s.username} · ${(s.roles||[]).join(', ')}`;await loadMessages();if(requestedCaseId>0)await openCase(requestedCaseId)}catch(e){inbox.innerHTML='<p class="error">'+esc(e.message)+'</p>'}}
async function loadMessages(button=null,reportError=true){if(inboxController)inboxController.abort();const controller=new AbortController();inboxController=controller;const label=button?button.textContent:'';if(button){button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='Загрузка…'}if(!conversations.length)inbox.innerHTML='<div class="empty">Загрузка...</div>';try{const d=await api('/message-center/status',{signal:controller.signal});if(inboxController!==controller)return {ok:false,aborted:true};conversations=d.items;renderMetrics(d);renderInbox();return {ok:true}}catch(e){if(e.name==='AbortError')return {ok:false,aborted:true};if(reportError)inbox.innerHTML='<p class="error">'+esc(e.message)+'</p>';return {ok:false,error:e.message}}finally{if(inboxController===controller){inboxController=null;if(button){button.disabled=false;button.removeAttribute('aria-busy');button.textContent=label}}}}
async function openCase(id,preserveStatus=false){if(sendPending&&id!==currentCaseId){feedback('Дождитесь завершения отправки текущего ответа.','warn');return {ok:false,busy:true}}if(dialogController)dialogController.abort();const controller=new AbortController();dialogController=controller;currentCaseId=Number(id);currentLatestMessageId=null;renderInbox();dialog.innerHTML='<div class="empty">Загрузка переписки...</div>';replyBox.classList.add('hidden');if(!preserveStatus)feedback('');try{const d=await api('/message-center/cases/'+currentCaseId+'/messages',{signal:controller.signal});if(dialogController!==controller||currentCaseId!==Number(id))return {ok:false,aborted:true};currentLatestMessageId=d.latest_message_id;dialogTitle.textContent='Переписка по делу '+d.case.number;caseMeta.textContent=(d.case.client_name||'Клиент')+(d.case.client_username?' · @'+d.case.client_username:'')+' · статус '+d.case.status+' · юрист '+(d.case.unassigned?'не назначен':d.case.lawyer_id);dialog.innerHTML=d.messages.length?d.messages.map(m=>{const meta=m.sender_type==='client'?(m.priority==='critical'?`<div class="message-meta"><span class="badge purple" title="${esc(m.priority_note||'')}">${esc(m.priority_label)}</span>${m.category?` <span class="badge gray">${esc(m.category)}</span>`:''}</div>`:(m.priority==='today'?`<div class="message-meta"><span class="badge amber" title="${esc(m.priority_note||'')}">${esc(m.priority_label)}</span>${m.category?` <span class="badge gray">${esc(m.category)}</span>`:''}</div>`:(m.category?`<div class="message-meta"><span class="badge gray">${esc(m.category)}</span></div>`:''))):'';return `<div class="msg ${m.sender_type==='client'?'client':'lawyer'}"><b>${m.sender_type==='client'?'Клиент':'Команда'}</b>${meta}<br>${esc(m.body??m.text)}<br><span class="muted">${esc(m.created_at||'')}</span></div>`}).join(''):'<div class="empty">Сообщений нет.</div>';dialog.scrollTop=dialog.scrollHeight;replyBox.classList.remove('hidden');const url=new URL(location.href);url.searchParams.set('case_id',String(currentCaseId));history.replaceState(null,'',url);await loadMessages(null,false);return {ok:true}}catch(e){if(e.name==='AbortError')return {ok:false,aborted:true};dialog.innerHTML=`<div class="error"><b>Не удалось открыть диалог</b><p>${esc(e.message)}</p><button onclick="openCase(${Number(id)})">Повторить</button></div>`;return {ok:false,error:e.message}}finally{if(dialogController===controller)dialogController=null}}
async function sendReply(button){if(sendPending||!currentCaseId)return;const caseId=currentCaseId,lastMessageId=currentLatestMessageId,text=replyText.value.trim();if(text.length<2){feedback('Введите ответ.','error');return}if(text.length>3800){feedback('Ответ не должен превышать 3800 символов.','error');return}sendPending=true;const label=button.textContent;button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='Сохранение…';replyText.disabled=true;feedback('Ответ сохраняется в переписке…');try{const result=await api('/message-center/cases/'+caseId+'/reply',{method:'POST',body:JSON.stringify({text,expected_last_message_id:lastMessageId})});if(currentCaseId===caseId){replyText.value='';document.getElementById('charCount').textContent='0';currentLatestMessageId=result.latest_message_id;const [deliveryText,deliveryState]=deliveryFeedback(result.delivery);feedback(deliveryText,deliveryState);const refreshed=await openCase(caseId,true);if(!refreshed.ok&&!refreshed.aborted)feedback(`${deliveryText} Но экран не обновился: ${refreshed.error}`,'warn')}}catch(e){feedback(`Ответ не отправлен и не сохранён: ${e.message}`,'error');if(e.status===409&&currentCaseId===caseId){const refreshed=await openCase(caseId,true);if(refreshed.ok)feedback('В переписке появились новые сообщения. Текст ответа сохранён в поле; проверьте диалог и отправьте повторно.','warn')}}finally{sendPending=false;if(currentCaseId===caseId){button.disabled=false;button.removeAttribute('aria-busy');button.textContent=label;replyText.disabled=false;replyText.focus()}}}
boot();setInterval(()=>{void loadMessages(null,false)},60000);
</script></body></html>
"""
