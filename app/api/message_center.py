from collections import defaultdict
from datetime import datetime, timezone

from aiogram import Bot
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.messages.message_service import MessageService
from app.models.case import Case
from app.models.message import Message
from app.models.user import User

router = APIRouter(tags=["message-center"])


class ReplyPayload(BaseModel):
    text: str
    lawyer_id: int | None = None


def check(token: str | None):
    if token != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="Неверный ADMIN_API_TOKEN")


def _iso(value):
    return value.isoformat() if value else None


def _age_minutes(value) -> int | None:
    if not value:
        return None
    now = datetime.now(timezone.utc)
    created = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return max(0, int((now - created).total_seconds() // 60))


@router.get("/message-center/status")
async def message_center_status(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Conversation queue grouped by case, including read and answered dialogs."""
    check(x_admin_token)

    messages = (
        await db.execute(
            select(Message)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1000)
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

    items = []
    total_unread = 0
    waiting_count = 0
    overdue_count = 0

    for case_id, case_messages in by_case.items():
        case = cases_by_id.get(case_id)
        if not case:
            continue
        client = clients_by_id.get(case.client_id)
        latest = case_messages[0]
        unread_count = sum(
            1
            for message in case_messages
            if message.sender_type == "client" and not message.is_read
        )
        waiting_for_reply = latest.sender_type == "client"
        age_minutes = _age_minutes(latest.created_at)
        overdue = bool(waiting_for_reply and age_minutes is not None and age_minutes >= 240)

        total_unread += unread_count
        waiting_count += int(waiting_for_reply)
        overdue_count += int(overdue)

        items.append(
            {
                "case_id": case.id,
                "case_number": case.case_number,
                "case_status": case.status,
                "lawyer_id": case.assigned_lawyer_id,
                "client_name": client.full_name if client else None,
                "client_username": client.telegram_username if client else None,
                "latest_message_id": latest.id,
                "latest_sender_type": latest.sender_type,
                "latest_text": latest.text[:1000],
                "latest_created_at": _iso(latest.created_at),
                "age_minutes": age_minutes,
                "unread_count": unread_count,
                "waiting_for_reply": waiting_for_reply,
                "overdue": overdue,
                "message_count": len(case_messages),
            }
        )

    items.sort(
        key=lambda item: (
            not item["overdue"],
            not item["waiting_for_reply"],
            item["latest_created_at"] or "",
        )
    )
    # Keep newest first within equal priority buckets.
    priority_groups: dict[tuple[bool, bool], list[dict]] = defaultdict(list)
    for item in items:
        priority_groups[(item["overdue"], item["waiting_for_reply"])].append(item)
    ordered = []
    for key in [(True, True), (False, True), (False, False)]:
        ordered.extend(
            sorted(
                priority_groups.get(key, []),
                key=lambda item: item["latest_created_at"] or "",
                reverse=True,
            )
        )

    return {
        "conversation_count": len(ordered),
        "unread_count": total_unread,
        "waiting_count": waiting_count,
        "overdue_count": overdue_count,
        "items": ordered,
    }


@router.get("/message-center/cases/{case_id}/messages")
async def case_messages(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalars().first()
    if not case:
        raise HTTPException(404, "Дело не найдено")
    client = (await db.execute(select(User).where(User.id == case.client_id))).scalars().first()

    service = MessageService(db)
    messages = await service.list_case_messages(case_id)
    await service.mark_client_messages_read(case_id)
    await db.commit()
    return {
        "case": {
            "id": case.id,
            "number": case.case_number,
            "status": case.status,
            "lawyer_id": case.assigned_lawyer_id,
            "client_name": client.full_name if client else None,
            "client_username": client.telegram_username if client else None,
        },
        "messages": [
            {
                "id": message.id,
                "sender_type": message.sender_type,
                "sender_id": message.sender_id,
                "text": message.text,
                "is_read": message.is_read,
                "created_at": _iso(message.created_at),
            }
            for message in messages
        ],
    }


@router.post("/message-center/cases/{case_id}/reply")
async def reply_to_client(
    case_id: int,
    payload: ReplyPayload,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    text = payload.text.strip()
    if len(text) < 2:
        raise HTTPException(400, "Введите текст ответа")
    if len(text) > 4000:
        raise HTTPException(400, "Ответ не должен превышать 4000 символов")

    case = (await db.execute(select(Case).where(Case.id == case_id))).scalars().first()
    if not case:
        raise HTTPException(404, "Дело не найдено")
    client = (await db.execute(select(User).where(User.id == case.client_id))).scalars().first()
    if not client:
        raise HTTPException(404, "Клиент не найден")

    lawyer_id = payload.lawyer_id or case.assigned_lawyer_id
    created = await MessageService(db).create_lawyer_message(
        case=case,
        lawyer_id=lawyer_id,
        text=text,
    )
    await MessageService(db).mark_client_messages_read(case_id)

    bot = Bot(token=settings.bot_token)
    try:
        await bot.send_message(
            chat_id=client.telegram_id,
            text=(
                "💬 Ответ юриста\n\n"
                f"Дело: {case.case_number}\n\n"
                f"{text}\n\n"
                "Ответ сохранён в переписке по делу."
            ),
        )
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            502,
            "Не удалось доставить ответ в Telegram. Ответ не сохранён; повторите отправку.",
        ) from exc
    finally:
        await bot.session.close()

    await db.commit()
    return {
        "ok": True,
        "message_id": created.id,
        "case_id": case.id,
        "telegram_delivered": True,
    }


@router.post("/message-center/{message_id}/read")
async def mark_message_read(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    message = (await db.execute(select(Message).where(Message.id == message_id))).scalars().first()
    if not message:
        raise HTTPException(404, "Сообщение не найдено")
    message.is_read = True
    await db.commit()
    return {"ok": True, "message_id": message.id, "is_read": message.is_read}


@router.get("/message-center/ui", response_class=HTMLResponse)
async def message_center_ui():
    return HTMLResponse(MESSAGE_CENTER_HTML)


MESSAGE_CENTER_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Digital Legal Concierge — сообщения</title>
  <style>
    :root{--blue:#2563eb;--green:#15803d;--red:#b91c1c;--amber:#b45309;--line:#e5e7eb;--muted:#6b7280}
    *{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}
    header{background:#111827;color:#fff;padding:18px 22px}header h1{margin:0 0 5px;font-size:22px}header p{margin:0;color:#d1d5db}
    main{padding:18px;max-width:1280px;margin:auto;display:grid;grid-template-columns:430px 1fr;gap:16px}
    .card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
    button,.button{background:var(--blue);color:#fff;border:0;border-radius:10px;padding:10px 13px;text-decoration:none;font-weight:700;cursor:pointer}
    button:disabled{opacity:.55;cursor:not-allowed}input,textarea,select{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:10px;margin:6px 0}
    textarea{min-height:130px;resize:vertical}.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.toolbar>*{width:auto}
    .metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}.metric{background:#f9fafb;border:1px solid var(--line);border-radius:12px;padding:10px}.metric b{font-size:21px;display:block}
    .item{border:1px solid var(--line);border-radius:12px;padding:12px;margin:9px 0;cursor:pointer}.item:hover{background:#f9fafb}.item.active{border-color:var(--blue);background:#eff6ff}
    .item.overdue{border-left:5px solid var(--red)}.item.waiting:not(.overdue){border-left:5px solid var(--amber)}.item.answered{border-left:5px solid var(--green)}
    .badges{display:flex;gap:6px;flex-wrap:wrap;margin:5px 0}.badge{font-size:12px;font-weight:700;border-radius:999px;padding:3px 8px;background:#eef2ff}.badge.red{background:#fee2e2;color:#991b1b}.badge.amber{background:#fef3c7;color:#92400e}.badge.green{background:#dcfce7;color:#166534}
    .msg{padding:11px 13px;border-radius:12px;margin:8px 0;white-space:pre-wrap}.client{background:#eef2ff;margin-right:12%}.lawyer{background:#ecfdf5;margin-left:12%}
    .muted{color:var(--muted);font-size:13px}.error{color:var(--red)}.ok{color:var(--green)}.hidden{display:none}.empty{padding:24px;text-align:center;color:var(--muted)}
    #dialog{max-height:52vh;overflow:auto;padding-right:4px}.counter{text-align:right;color:var(--muted);font-size:12px}
    @media(max-width:900px){main{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}.client{margin-right:4%}.lawyer{margin-left:4%}}
  </style>
</head>
<body>
<header><h1>💬 Центр сообщений</h1><p>Очередь вопросов, SLA и ответы клиентам в Telegram.</p></header>
<main>
  <section class="card">
    <h2>Диалоги</h2>
    <input id="token" type="password" placeholder="ADMIN_API_TOKEN" autocomplete="off">
    <div class="toolbar">
      <button onclick="loadMessages()">Обновить</button>
      <select id="filter" onchange="renderInbox()">
        <option value="all">Все диалоги</option>
        <option value="waiting">Ожидают ответа</option>
        <option value="overdue">Просрочены 4+ часа</option>
        <option value="unread">Непрочитанные</option>
        <option value="answered">Ответ отправлен</option>
      </select>
      <a class="button" href="/operator">Оператор</a>
    </div>
    <div id="metrics" class="metrics"></div>
    <div id="inbox" class="muted">Введите токен и нажмите «Обновить».</div>
  </section>
  <section class="card">
    <h2 id="dialogTitle">Переписка</h2>
    <div id="caseMeta" class="muted"></div>
    <div id="dialog" class="empty">Выберите диалог слева.</div>
    <div id="replyBox" class="hidden">
      <textarea id="replyText" maxlength="4000" placeholder="Ответ юриста клиенту"></textarea>
      <div class="counter"><span id="charCount">0</span>/4000</div>
      <button id="sendButton" onclick="sendReply()">Отправить клиенту</button>
      <span id="replyStatus"></span>
    </div>
  </section>
</main>
<script>
let currentCaseId=null, conversations=[];
const tokenInput=document.getElementById('token'), inbox=document.getElementById('inbox'), metrics=document.getElementById('metrics');
const dialog=document.getElementById('dialog'), dialogTitle=document.getElementById('dialogTitle'), caseMeta=document.getElementById('caseMeta');
const replyBox=document.getElementById('replyBox'), replyText=document.getElementById('replyText'), replyStatus=document.getElementById('replyStatus'), sendButton=document.getElementById('sendButton');
tokenInput.value=localStorage.getItem('admin_token')||'';
replyText.addEventListener('input',()=>document.getElementById('charCount').textContent=replyText.value.length);
function headers(){return {'x-admin-token':tokenInput.value,'Content-Type':'application/json'}}
function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
function ageLabel(minutes){if(minutes==null)return '';if(minutes<60)return minutes+' мин';const h=Math.floor(minutes/60),m=minutes%60;return h+' ч'+(m?' '+m+' мин':'')}
function renderMetrics(data){metrics.innerHTML=`<div class="metric"><b>${data.conversation_count}</b><span class="muted">диалогов</span></div><div class="metric"><b>${data.unread_count}</b><span class="muted">непрочитано</span></div><div class="metric"><b>${data.waiting_count}</b><span class="muted">ждут ответа</span></div><div class="metric"><b>${data.overdue_count}</b><span class="muted">просрочено</span></div>`}
function renderInbox(){
  const filter=document.getElementById('filter').value;
  const rows=conversations.filter(x=>filter==='all'||(filter==='waiting'&&x.waiting_for_reply)||(filter==='overdue'&&x.overdue)||(filter==='unread'&&x.unread_count>0)||(filter==='answered'&&!x.waiting_for_reply));
  if(!rows.length){inbox.innerHTML='<div class="empty">По выбранному фильтру диалогов нет.</div>';return}
  inbox.innerHTML=rows.map(x=>{
    const cls=x.overdue?'overdue':(x.waiting_for_reply?'waiting':'answered');
    const status=x.overdue?'<span class="badge red">Просрочено</span>':(x.waiting_for_reply?'<span class="badge amber">Ждёт ответа</span>':'<span class="badge green">Ответ отправлен</span>');
    const unread=x.unread_count?`<span class="badge">Новых: ${x.unread_count}</span>`:'';
    return `<div id="case_${x.case_id}" class="item ${cls} ${currentCaseId===x.case_id?'active':''}" onclick="openCase(${x.case_id})"><b>${esc(x.case_number)}</b> · ${esc(x.client_name||'Клиент')}<div class="badges">${status}${unread}</div><div>${esc(x.latest_text)}</div><div class="muted">${x.latest_sender_type==='client'?'Клиент':'Юрист'} · ${ageLabel(x.age_minutes)} · сообщений: ${x.message_count}</div></div>`;
  }).join('');
}
async function loadMessages(){
  localStorage.setItem('admin_token',tokenInput.value);
  inbox.innerHTML='<div class="empty">Загрузка...</div>';
  try{
    const r=await fetch('/message-center/status',{headers:headers()}),data=await r.json();
    if(!r.ok)throw new Error(data.detail||'Ошибка загрузки');
    conversations=data.items;renderMetrics(data);renderInbox();
  }catch(e){inbox.innerHTML='<p class="error">'+esc(e.message)+'</p>'}
}
async function openCase(caseId){
  currentCaseId=caseId;renderInbox();dialog.innerHTML='<div class="empty">Загрузка переписки...</div>';
  try{
    const r=await fetch('/message-center/cases/'+caseId+'/messages',{headers:headers()}),data=await r.json();
    if(!r.ok)throw new Error(data.detail||'Ошибка загрузки');
    dialogTitle.textContent='Переписка по делу '+data.case.number;
    caseMeta.textContent=(data.case.client_name||'Клиент')+(data.case.client_username?' · @'+data.case.client_username:'')+' · статус '+data.case.status+' · юрист '+(data.case.lawyer_id||'не назначен');
    dialog.innerHTML=data.messages.length?data.messages.map(m=>`<div class="msg ${m.sender_type==='client'?'client':'lawyer'}"><b>${m.sender_type==='client'?'Клиент':'Юрист'}</b><br>${esc(m.text)}<br><span class="muted">${esc(m.created_at||'')}</span></div>`).join(''):'<div class="empty">Сообщений нет.</div>';
    dialog.scrollTop=dialog.scrollHeight;replyBox.classList.remove('hidden');replyStatus.textContent='';await loadMessages();
  }catch(e){dialog.innerHTML='<p class="error">'+esc(e.message)+'</p>'}
}
async function sendReply(){
  if(!currentCaseId)return;
  const text=replyText.value.trim();if(text.length<2){replyStatus.innerHTML='<span class="error">Введите ответ.</span>';return}
  sendButton.disabled=true;replyStatus.textContent='Отправка...';
  try{
    const r=await fetch('/message-center/cases/'+currentCaseId+'/reply',{method:'POST',headers:headers(),body:JSON.stringify({text})}),data=await r.json();
    if(!r.ok)throw new Error(data.detail||'Ошибка отправки');
    replyText.value='';document.getElementById('charCount').textContent='0';replyStatus.innerHTML='<span class="ok">Ответ доставлен в Telegram.</span>';await openCase(currentCaseId);
  }catch(e){replyStatus.innerHTML='<span class="error">'+esc(e.message)+'</span>'}finally{sendButton.disabled=false}
}
if(tokenInput.value)loadMessages();
setInterval(()=>{if(tokenInput.value)loadMessages()},60000);
</script>
</body>
</html>
"""