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
        "business_timezone": settings.business_timezone,
        "business_timezone_label": settings.business_timezone_label,
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
:root{--bg:#f4f6fa;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--green:#166534;--red:#b42318;--amber:#a15c00}
*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:var(--bg);color:var(--ink)}
header{background:#111827;color:white;padding:18px 24px}header h1{font-size:22px;margin:0 0 5px}header p{margin:0;color:#d0d5dd;font-size:13px;line-height:1.45}main{padding:20px;max-width:1100px;margin:auto}
.card{background:white;border:1px solid var(--line);border-radius:16px;padding:16px;margin:12px 0}.section-label{font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:7px}
.actions{display:flex;gap:8px;flex-wrap:wrap}button,.button{display:inline-block;background:var(--blue);color:white;border:0;border-radius:10px;padding:10px 14px;text-decoration:none;font-weight:750;cursor:pointer}.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}
.item{border-top:1px solid var(--line);padding:11px 0;line-height:1.45}.ok{color:var(--green)}.bad{color:var(--red)}.muted{color:var(--muted);font-size:13px}.next{margin-top:12px;padding:12px;border:1px solid #c7d7fe;background:#f5f8ff;border-radius:12px}pre{background:#0b1020;color:#d1e7ff;padding:12px;border-radius:10px;overflow:auto}@media(max-width:680px){main{padding:12px}.actions>*{width:100%;text-align:center}}
</style>
</head>
<body>
<header><h1>🧾 Audit Center</h1><p>Роль: суперадминистратор · неизменяемый журнал действий с HMAC-цепочкой целостности · <span id="tz">время загружается…</span></p></header>
<main>
<section class="card"><div class="section-label">Сейчас</div><div id="integrity">Проверка целостности…</div><div id="next" class="next"><b>Главный следующий шаг</b><div class="muted">Дождитесь проверки цепочки.</div></div></section>
<section class="card"><div class="section-label">Вторичные действия</div><div class="actions"><button onclick="loadAudit()">Обновить проверку</button><a class="button secondary" href="/admin/workdesk/ui">Рабочий стол</a></div></section>
<section class="card"><div class="section-label">Последние события</div><div id="out">Загрузка журнала…</div></section>
</main>
<script>
let token='',businessTimeZone='UTC',businessTimeLabel='UTC';
function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function dt(value){if(!value)return '—';try{const rendered=new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:businessTimeZone}).format(new Date(value));return businessTimeLabel?rendered+' '+businessTimeLabel:rendered}catch(_){return String(value)}}
async function api(path){const response=await fetch(path,{credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token}});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||'Ошибка');return data}
async function boot(){const response=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!response.ok){location.href='/login';return}const session=await response.json();if(!(session.roles||[session.role]).includes('superadmin')){integrity.innerHTML='<span class="bad">Недостаточно прав.</span>';out.textContent='';next.innerHTML='<b>Главный следующий шаг</b><div class="bad">Вернитесь в доступное рабочее пространство.</div>';return}token=session.api_token;businessTimeZone=session.business_timezone||'UTC';businessTimeLabel=session.business_timezone_label||businessTimeZone;tz.textContent='время: '+businessTimeLabel;await loadAudit()}
async function loadAudit(){try{const data=await api('/audit-center/status?limit=150');businessTimeZone=data.business_timezone||businessTimeZone;businessTimeLabel=data.business_timezone_label||businessTimeLabel;tz.textContent='время: '+businessTimeLabel;const check=data.integrity;integrity.innerHTML=check.ok?`<b class="ok">Цепочка подтверждена</b><div class="muted">Проверено событий: ${esc(check.checked_count)} · ключи: ${esc((check.key_ids||[]).join(', '))}<br>последний хеш: ${esc(check.last_verified_hash)}</div>`:`<b class="bad">Нарушение целостности</b><pre>${esc(JSON.stringify(check.first_invalid,null,2))}</pre>`;next.innerHTML=check.ok?'<b>Главный следующий шаг</b><div>Критических действий не требуется. Используйте журнал для точечной проверки события или вернитесь в рабочий стол.</div>':'<b>Главный следующий шаг</b><div class="bad">Не выполняйте корректирующие изменения журнала вручную. Зафиксируйте первое повреждённое событие и проведите разбор целостности.</div>';out.innerHTML=`<b>Событий в выборке: ${esc(data.count)}</b>`+data.items.map(a=>`<div class="item"><b>#${esc(a.chain_sequence)} · ${esc(a.action)}</b> · ${esc(a.entity_type)} #${esc(a.entity_id)}<br>Кто: ${esc(a.actor_type)} ${esc(a.actor_id||'')}<br>${esc(a.comment||'')}<br><span class="muted">${esc(dt(a.created_at))} · key ${esc(a.integrity_key_id||'')} · hash ${esc(a.event_hash_prefix||'')}</span></div>`).join('')}catch(error){integrity.innerHTML='<span class="bad">'+esc(error.message)+'</span>';next.innerHTML='<b>Главный следующий шаг</b><div class="bad">Обновите проверку. Если ошибка повторяется, вернитесь в рабочий стол и не выполняйте действия, требующие доверия к журналу.</div>';out.textContent=''}}
boot();
</script>
</body>
</html>
"""