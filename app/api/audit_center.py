from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.security.access_control import ROLE_SUPERADMIN
from app.security.audit_integrity import verify_audit_chain
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["audit-center"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def require_audit_superadmin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(
            status_code=403,
            detail="Доступ к журналу только для суперадминистратора",
        )
    return actor


@router.get("/audit-center/status")
async def audit_center_status(
    request: Request,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await require_audit_superadmin(request, db, x_admin_token)
    limit = min(max(limit, 1), 500)
    rows = (
        await db.execute(
            select(AuditLog)
            .order_by(AuditLog.chain_sequence.desc(), AuditLog.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    integrity = await verify_audit_chain(db)
    return {
        "count": len(rows),
        "integrity": integrity,
        "items": [
            {
                "id": audit.id,
                "chain_sequence": audit.chain_sequence,
                "actor_type": audit.actor_type,
                "actor_id": audit.actor_id,
                "action": audit.action,
                "entity_type": audit.entity_type,
                "entity_id": audit.entity_id,
                "comment": audit.comment,
                "integrity_key_id": audit.integrity_key_id,
                "event_hash_prefix": audit.event_hash[:12] if audit.event_hash else None,
                "created_at": audit.created_at.isoformat() if audit.created_at else None,
            }
            for audit in rows
        ],
    }


@router.get("/audit-center/integrity")
async def audit_integrity_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await require_audit_superadmin(request, db, x_admin_token)
    return await verify_audit_chain(db)


@router.get("/audit-center/ui", response_class=HTMLResponse)
async def audit_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await require_audit_superadmin(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return HTMLResponse(AUDIT_CENTER_HTML)


AUDIT_CENTER_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audit Center</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}
header{background:#111827;color:white;padding:22px}main{padding:22px;max-width:1100px;margin:auto}
.card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:16px;margin:12px 0}
button,.button{display:inline-block;background:#2563eb;color:white;border:0;border-radius:10px;padding:10px 14px;text-decoration:none;font-weight:700;cursor:pointer}
.item{border-top:1px solid #e5e7eb;padding:10px 0}.ok{color:#166534}.bad{color:#991b1b}.muted{color:#6b7280;font-size:13px}
pre{background:#0b1020;color:#d1e7ff;padding:12px;border-radius:10px;overflow:auto}
</style>
</head>
<body>
<header><h1>🧾 Audit Center</h1><p>Неизменяемый журнал действий с HMAC-цепочкой целостности.</p></header>
<main>
<section class="card"><button onclick="loadAudit()">Обновить проверку</button> <a class="button" href="/admin/workdesk/ui">Рабочий стол</a></section>
<section class="card"><div id="integrity">Проверка целостности…</div></section>
<section class="card"><div id="out">Загрузка журнала…</div></section>
</main>
<script>
let token='';
function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function api(path){const response=await fetch(path,{credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token}});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||'Ошибка');return data}
async function boot(){const response=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!response.ok){location.href='/login';return}const session=await response.json();if(!(session.roles||[session.role]).includes('superadmin')){integrity.innerHTML='<span class="bad">Недостаточно прав.</span>';out.textContent='';return}token=session.api_token;await loadAudit()}
async function loadAudit(){try{const data=await api('/audit-center/status?limit=150');const check=data.integrity;integrity.innerHTML=check.ok?`<b class="ok">Цепочка подтверждена</b><div class="muted">Проверено событий: ${esc(check.checked_count)} · ключи: ${esc((check.key_ids||[]).join(', '))}<br>последний хеш: ${esc(check.last_verified_hash)}</div>`:`<b class="bad">Нарушение целостности</b><pre>${esc(JSON.stringify(check.first_invalid,null,2))}</pre>`;out.innerHTML=`<b>Последние события: ${esc(data.count)}</b>`+data.items.map(a=>`<div class="item"><b>#${esc(a.chain_sequence)} · ${esc(a.action)}</b> · ${esc(a.entity_type)} #${esc(a.entity_id)}<br>Кто: ${esc(a.actor_type)} ${esc(a.actor_id||'')}<br>${esc(a.comment||'')}<br><span class="muted">${esc(a.created_at||'')} · key ${esc(a.integrity_key_id||'')} · hash ${esc(a.event_hash_prefix||'')}</span></div>`).join('')}catch(error){integrity.innerHTML='<span class="bad">'+esc(error.message)+'</span>';out.textContent=''}}
boot();
</script>
</body>
</html>
"""
