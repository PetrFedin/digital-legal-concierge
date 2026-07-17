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
        raise HTTPException(status_code=401, detail="bad token")


@router.get("/message-center/status")
async def message_center_status(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    unread = (
        await db.execute(
            select(Message)
            .where(
                Message.sender_type == "client",
                Message.is_read.is_(False),
            )
            .order_by(Message.created_at.desc())
            .limit(200)
        )
    ).scalars().all()
    return {
        "unread_count": len(unread),
        "items": [
            {
                "id": message.id,
                "case_id": message.case_id,
                "sender_type": message.sender_type,
                "sender_id": message.sender_id,
                "text": message.text[:1000],
                "created_at": message.created_at.isoformat() if message.created_at else None,
            }
            for message in unread
        ],
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
        raise HTTPException(404, "case not found")

    service = MessageService(db)
    messages = await service.list_case_messages(case_id)
    await service.mark_client_messages_read(case_id)
    await db.commit()
    return {
        "case": {
            "id": case.id,
            "number": case.case_number,
            "lawyer_id": case.assigned_lawyer_id,
        },
        "messages": [
            {
                "id": message.id,
                "sender_type": message.sender_type,
                "sender_id": message.sender_id,
                "text": message.text,
                "is_read": message.is_read,
                "created_at": message.created_at.isoformat() if message.created_at else None,
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
        raise HTTPException(400, "reply text is required")
    if len(text) > 4000:
        raise HTTPException(400, "reply is too long")

    case = (await db.execute(select(Case).where(Case.id == case_id))).scalars().first()
    if not case:
        raise HTTPException(404, "case not found")
    client = (await db.execute(select(User).where(User.id == case.client_id))).scalars().first()
    if not client:
        raise HTTPException(404, "client not found")

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
        raise HTTPException(502, f"Telegram delivery failed: {type(exc).__name__}") from exc
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
        raise HTTPException(404, "message not found")
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
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}
    header{background:#111827;color:#fff;padding:20px}
    main{padding:20px;max-width:1100px;margin:auto;display:grid;grid-template-columns:420px 1fr;gap:16px}
    .card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px}
    button,.button{background:#2563eb;color:#fff;border:0;border-radius:10px;padding:10px 13px;text-decoration:none;font-weight:700;cursor:pointer}
    input,textarea{width:100%;box-sizing:border-box;padding:10px;border:1px solid #d1d5db;border-radius:10px;margin:6px 0}
    textarea{min-height:120px;resize:vertical}.item{border-top:1px solid #e5e7eb;padding:12px 0;cursor:pointer}.item:hover{background:#f9fafb}
    .msg{padding:10px 12px;border-radius:12px;margin:8px 0;white-space:pre-wrap}.client{background:#eef2ff}.lawyer{background:#ecfdf5}
    .muted{color:#6b7280;font-size:13px}.error{color:#b91c1c}.ok{color:#15803d}
    @media(max-width:800px){main{grid-template-columns:1fr}}
  </style>
</head>
<body>
<header><h1>💬 Центр сообщений</h1><p>Входящие вопросы и ответы клиентам в Telegram.</p></header>
<main>
  <section class="card">
    <h2>Входящие</h2>
    <input id="token" type="password" placeholder="ADMIN_API_TOKEN">
    <button onclick="loadMessages()">Обновить</button>
    <a class="button" href="/operator">Оператор</a>
    <div id="inbox" class="muted">Введите токен и загрузите сообщения.</div>
  </section>
  <section class="card">
    <h2 id="dialogTitle">Переписка</h2>
    <div id="dialog" class="muted">Выберите сообщение слева.</div>
    <div id="replyBox" style="display:none">
      <textarea id="replyText" placeholder="Ответ юриста клиенту"></textarea>
      <button onclick="sendReply()">Отправить клиенту</button>
      <span id="replyStatus"></span>
    </div>
  </section>
</main>
<script>
let currentCaseId = null;
const tokenInput = document.getElementById('token');
tokenInput.value = localStorage.getItem('admin_token') || '';
function headers(){return {'x-admin-token':tokenInput.value,'Content-Type':'application/json'}}
function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
async function loadMessages(){
  localStorage.setItem('admin_token',tokenInput.value);
  const r=await fetch('/message-center/status',{headers:headers()}); const data=await r.json();
  if(!r.ok){inbox.innerHTML='<p class="error">'+esc(data.detail||'Ошибка')+'</p>';return}
  inbox.innerHTML='<p><b>Непрочитано: '+data.unread_count+'</b></p>'+(data.items.length?data.items.map(m=>`<div class="item" onclick="openCase(${m.case_id})"><b>Сообщение #${m.id}</b><br>Дело ${m.case_id}<br>${esc(m.text)}<br><span class="muted">${esc(m.created_at||'')}</span></div>`).join(''):'<p class="muted">Новых сообщений нет.</p>');
}
async function openCase(caseId){
  currentCaseId=caseId;
  const r=await fetch('/message-center/cases/'+caseId+'/messages',{headers:headers()}); const data=await r.json();
  if(!r.ok){dialog.innerHTML='<p class="error">'+esc(data.detail||'Ошибка')+'</p>';return}
  dialogTitle.textContent='Переписка по делу '+data.case.number;
  dialog.innerHTML=data.messages.length?data.messages.map(m=>`<div class="msg ${m.sender_type==='client'?'client':'lawyer'}"><b>${m.sender_type==='client'?'Клиент':'Юрист'}</b><br>${esc(m.text)}<br><span class="muted">${esc(m.created_at||'')}</span></div>`).join(''):'<p class="muted">Сообщений нет.</p>';
  replyBox.style.display='block'; replyStatus.textContent=''; await loadMessages();
}
async function sendReply(){
  const text=replyText.value.trim(); if(text.length<2){replyStatus.innerHTML='<span class="error">Введите ответ.</span>';return}
  replyStatus.textContent='Отправка...';
  const r=await fetch('/message-center/cases/'+currentCaseId+'/reply',{method:'POST',headers:headers(),body:JSON.stringify({text})}); const data=await r.json();
  if(!r.ok){replyStatus.innerHTML='<span class="error">'+esc(data.detail||'Ошибка отправки')+'</span>';return}
  replyText.value=''; replyStatus.innerHTML='<span class="ok">Ответ доставлен в Telegram.</span>'; await openCase(currentCaseId);
}
</script>
</body>
</html>
"""
