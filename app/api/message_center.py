from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.case import Case
from app.models.message import Message
from app.models.user import User

router = APIRouter(tags=["message-center"])


def check(token: str | None):
    if token != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="bad token")


@router.get("/message-center/status")
async def message_center_status(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    unread = (await db.execute(select(Message).where(Message.is_read.is_(False)).order_by(Message.created_at.desc()).limit(200))).scalars().all()
    return {
        "unread_count": len(unread),
        "items": [
            {
                "id": m.id,
                "case_id": m.case_id,
                "sender_type": m.sender_type,
                "sender_id": m.sender_id,
                "text": m.text[:300],
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in unread
        ],
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
    html = """
    <!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Message Center v23</title>
    <style>
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}header{background:#111827;color:#fff;padding:22px}main{padding:22px;max-width:1100px;margin:auto}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px;margin:12px 0}button,.button{display:inline-block;background:#2563eb;color:white;border:0;border-radius:10px;padding:10px 14px;text-decoration:none;font-weight:700}input{padding:10px;border:1px solid #d1d5db;border-radius:10px;min-width:320px}pre{background:#0b1020;color:#d1e7ff;padding:12px;border-radius:10px;overflow:auto}.item{border-top:1px solid #e5e7eb;padding:10px 0}
    </style></head><body><header><h1>💬 Message Center v23</h1><p>Центр входящих сообщений клиента по делам.</p></header><main>
    <section class='card'><h2>Подключение</h2><p>Введите Admin API Token из .env. Без него центр не покажет сообщения.</p><input id='token' placeholder='ADMIN_API_TOKEN'><button onclick='loadMessages()'>Загрузить</button> <a class='button' href='/operator'>Оператор</a></section>
    <section class='card'><h2>Непрочитанные сообщения</h2><div id='out'>Нажмите «Загрузить».</div></section>
    <script>
    async function loadMessages(){const token=document.getElementById('token').value; const r=await fetch('/message-center/status',{headers:{'x-admin-token':token}}); const data=await r.json(); if(!r.ok){out.innerHTML='<pre>'+JSON.stringify(data,null,2)+'</pre>';return} out.innerHTML='<b>Непрочитано: '+data.unread_count+'</b>'+data.items.map(m=>`<div class='item'><b>#${m.id}</b> дело ${m.case_id}, ${m.sender_type}<br>${m.text}<br><button onclick='markRead(${m.id})'>Прочитано</button></div>`).join('')}
    async function markRead(id){const token=document.getElementById('token').value; await fetch('/message-center/'+id+'/read',{method:'POST',headers:{'x-admin-token':token}}); loadMessages()}
    </script></main></body></html>
    """
    return HTMLResponse(html)
