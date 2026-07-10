from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.audit_log import AuditLog

router = APIRouter(tags=["audit-center"])


def check(token: str | None):
    if token != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="bad token")


@router.get("/audit-center/status")
async def audit_center_status(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    limit = min(max(limit, 1), 500)
    rows = (await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))).scalars().all()
    return {
        "count": len(rows),
        "items": [
            {
                "id": a.id,
                "actor_type": a.actor_type,
                "actor_id": a.actor_id,
                "action": a.action,
                "entity_type": a.entity_type,
                "entity_id": a.entity_id,
                "comment": a.comment,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ],
    }


@router.get("/audit-center/ui", response_class=HTMLResponse)
async def audit_center_ui():
    html = """
    <!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Audit Center v23</title><style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}header{background:#111827;color:white;padding:22px}main{padding:22px;max-width:1100px;margin:auto}.card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:16px;margin:12px 0}button,.button{display:inline-block;background:#2563eb;color:white;border:0;border-radius:10px;padding:10px 14px;text-decoration:none;font-weight:700}input{padding:10px;border:1px solid #d1d5db;border-radius:10px;min-width:320px}.item{border-top:1px solid #e5e7eb;padding:10px 0}pre{background:#0b1020;color:#d1e7ff;padding:12px;border-radius:10px;overflow:auto}</style></head><body>
    <header><h1>🧾 Audit Center v23</h1><p>Журнал действий: статусы, оплаты, документы, решения юриста и администратора.</p></header><main>
    <section class='card'><input id='token' placeholder='ADMIN_API_TOKEN'><button onclick='loadAudit()'>Загрузить журнал</button> <a class='button' href='/operator'>Оператор</a></section>
    <section class='card'><div id='out'>Нажмите «Загрузить журнал».</div></section>
    <script>
    async function loadAudit(){const token=document.getElementById('token').value; const r=await fetch('/audit-center/status?limit=150',{headers:{'x-admin-token':token}}); const data=await r.json(); if(!r.ok){out.innerHTML='<pre>'+JSON.stringify(data,null,2)+'</pre>';return} out.innerHTML='<b>Событий: '+data.count+'</b>'+data.items.map(a=>`<div class='item'><b>${a.action}</b> · ${a.entity_type} #${a.entity_id}<br>Кто: ${a.actor_type} ${a.actor_id||''}<br>${a.comment||''}<br><small>${a.created_at||''}</small></div>`).join('')}
    </script></main></body></html>
    """
    return HTMLResponse(html)
