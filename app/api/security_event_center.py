from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.login_security_state import LoginSecurityState
from app.models.revoked_access_token import RevokedAccessToken
from app.security.access_control import ROLE_SUPERADMIN, normalize_roles
from app.security.audit_integrity import verify_audit_chain
from app.security.document_access import DocumentAccessError, resolve_document_actor
from app.security.keyring import security_key_status
from app.security.security_events import sanitize_security_details, security_event_severity

router = APIRouter(prefix="/security-events", tags=["security-events"])
SECURITY_ACTIONS = {"DOCUMENT_UPLOAD_REJECTED"}


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def require_security_superadmin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(403, "Доступ только для суперадминистратора")
    return actor


def _event_summary(audit: AuditLog) -> str:
    labels = {
        "security.admin_login_locked": "Административный вход временно заблокирован",
        "security.admin_password_authenticated": "Пароль административного пользователя подтверждён",
        "security.origin_blocked": "Отклонён cross-site запрос с cookie-сессией",
        "security.mfa_locked": "MFA временно заблокирована",
        "security.mfa_login": "Выполнен вход с MFA",
        "security.mfa_reset_by_superadmin": "Суперадминистратор сбросил MFA",
        "security.mfa_recovery_codes_rotated": "Резервные MFA-коды обновлены",
        "security.session_logged_out": "Административная сессия завершена и отозвана",
        "DOCUMENT_UPLOAD_REJECTED": "Небезопасный документ отклонён",
    }
    return labels.get(audit.action, audit.comment or audit.action)


def _public_details(audit: AuditLog) -> dict[str, Any]:
    details = sanitize_security_details(audit.new_value or {})
    if audit.action == "DOCUMENT_UPLOAD_REJECTED":
        details = {
            "reason_code": details.get("reason_code"),
            "document_type": details.get("type"),
            "sha256_prefix": str(details.get("sha256") or "")[:12] or None,
        }
    return details


@router.get("/status")
async def security_event_status(
    request: Request,
    hours: int = 24,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await require_security_superadmin(request, db, x_admin_token)
    hours = min(max(int(hours), 1), 24 * 30)
    limit = min(max(int(limit), 1), 500)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    rows = (
        await db.execute(
            select(AuditLog)
            .where(
                AuditLog.created_at >= cutoff,
                or_(
                    AuditLog.action.like("security.%"),
                    AuditLog.action.in_(SECURITY_ACTIONS),
                ),
            )
            .order_by(AuditLog.chain_sequence.desc(), AuditLog.id.desc())
            .limit(limit)
        )
    ).scalars().all()

    active_locks = int(
        (
            await db.execute(
                select(func.count())
                .select_from(LoginSecurityState)
                .where(LoginSecurityState.locked_until > now)
            )
        ).scalar_one()
        or 0
    )
    active_revocations = int(
        (
            await db.execute(
                select(func.count())
                .select_from(RevokedAccessToken)
                .where(RevokedAccessToken.expires_at > now)
            )
        ).scalar_one()
        or 0
    )
    administrators = (
        await db.execute(select(AdminUser).where(AdminUser.is_active.is_(True)))
    ).scalars().all()
    superadmins_without_mfa = [
        user.id
        for user in administrators
        if ROLE_SUPERADMIN in normalize_roles(user.role) and not user.mfa_enabled
    ]

    integrity = await verify_audit_chain(db)
    key_status = security_key_status()
    counts = {"critical": 0, "warning": 0, "info": 0}
    items = []
    for audit in rows:
        severity = security_event_severity(audit.action, audit.new_value)
        counts[severity] += 1
        items.append(
            {
                "id": audit.id,
                "chain_sequence": audit.chain_sequence,
                "severity": severity,
                "action": audit.action,
                "summary": _event_summary(audit),
                "actor_id": audit.actor_id,
                "entity_type": audit.entity_type,
                "entity_id": audit.entity_id,
                "details": _public_details(audit),
                "created_at": audit.created_at.isoformat() if audit.created_at else None,
            }
        )

    hard_failures = []
    if not integrity["ok"]:
        hard_failures.append("audit_integrity")
    if not key_status["ok"]:
        hard_failures.append("security_keys")
    if superadmins_without_mfa:
        hard_failures.append("superadmin_mfa")

    status = "critical" if hard_failures else "ok"
    if status == "ok" and (counts["critical"] or counts["warning"] or active_locks):
        status = "attention"

    return {
        "status": status,
        "window_hours": hours,
        "event_count": len(rows),
        "counts": counts,
        "active_login_locks": active_locks,
        "active_revoked_sessions": active_revocations,
        "superadmins_without_mfa": superadmins_without_mfa,
        "hard_failures": hard_failures,
        "audit_integrity": integrity,
        "security_keys": key_status,
        "business_timezone": settings.business_timezone,
        "business_timezone_label": settings.business_timezone_label,
        "generated_at": now.isoformat(),
        "items": items,
    }


@router.get("/ui", response_class=HTMLResponse)
async def security_event_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await require_security_superadmin(request, db, x_admin_token)
    except (DocumentAccessError, HTTPException) as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/operator", status_code=303)
        raise
    return HTMLResponse(SECURITY_EVENT_HTML)


SECURITY_EVENT_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Контроль безопасности</title>
<style>
:root{--bg:#f4f6fa;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--green:#166534;--red:#b42318;--amber:#a15c00;--green-soft:#ecfdf3;--red-soft:#fef3f2;--amber-soft:#fff7e6;--blue-soft:#eef2ff}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:var(--bg);color:var(--ink)}header{padding:20px 24px;background:#111827;color:white}header .inner{max-width:1180px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:16px}header h1{margin:0 0 4px;font-size:23px}header p{margin:0;color:#d0d5dd;font-size:13px;line-height:1.45}.links{display:flex;gap:8px;flex-wrap:wrap}main{max-width:1180px;margin:auto;padding:20px}.card{background:white;border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:14px}.section-label{font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:7px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.metric-card{background:white;border:1px solid var(--line);border-radius:14px;padding:13px}.metric{font-size:26px;font-weight:850}.muted{color:var(--muted);font-size:13px;line-height:1.45}.event{border-top:1px solid var(--line);padding:12px 0}.event:first-child{border-top:0}.critical{color:var(--red)}.warning{color:var(--amber)}.info{color:var(--blue)}.ok{color:var(--green)}.button,button{display:inline-block;background:var(--blue);color:white;padding:9px 12px;border-radius:10px;text-decoration:none;font-weight:750;border:0;cursor:pointer}.secondary{background:white;color:var(--ink);border:1px solid var(--line)}.button:focus-visible,button:focus-visible{outline:3px solid #c7d2fe;outline-offset:2px}.next{margin-top:12px;padding:12px;border:1px solid #c7d7fe;background:#f5f8ff;border-radius:12px}.next.critical{border-color:#fecdca;background:var(--red-soft)}.next.warning{border-color:#fedf89;background:var(--amber-soft)}.checks{line-height:1.7}.status{min-height:22px;margin-top:8px}pre{white-space:pre-wrap;background:#0b1020;color:#dbeafe;border-radius:10px;padding:10px;overflow:auto}@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){header .inner{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:1fr}main{padding:12px}.links>*{width:100%;text-align:center}}
</style>
</head>
<body>
<header><div class="inner"><div><h1>🛡 Контроль безопасности</h1><p>Роль: суперадминистратор · вход, MFA, защита запросов, документы и целостность аудита · <span id="timeContext">время загружается…</span></p></div><div class="links"><a class="button secondary" href="/operator">Руководство и контроль</a><a class="button secondary" href="/audit-center/ui">Аудит</a></div></div></header>
<main>
<section class="card"><div class="section-label">Сейчас</div><div id="summary" class="grid"><div class="metric-card"><div class="muted">Статус</div><div class="metric">—</div></div></div><div id="freshness" class="muted"></div><div id="next" class="next"><b>Главный следующий шаг</b><div class="muted">Проверяем критические контуры безопасности.</div></div></section>
<section class="card"><div class="section-label">Вторичные действия</div><div class="links"><button onclick="loadData()">Обновить проверку</button><a class="button secondary" href="/audit-center/ui">Проверить аудит</a><a class="button secondary" href="/admin/workdesk/ui">К рабочему столу</a></div><div id="status" class="status muted" role="status" aria-live="polite"></div></section>
<section class="card"><div class="section-label">Критические проверки</div><div id="checks" class="checks">Загрузка…</div></section>
<section class="card"><div class="section-label">Последние события</div><div id="events">Загрузка…</div></section>
<script>
let token='',businessTimeZone='UTC',businessTimeLabel='UTC';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function dt(value){if(!value)return '—';try{const rendered=new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:businessTimeZone}).format(new Date(value));return businessTimeLabel?rendered+' '+businessTimeLabel:rendered}catch(_){return String(value)}}
async function api(path){const r=await fetch(path,{credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token}});const d=await r.json().catch(()=>({}));if(r.status===401){location.href='/login';throw new Error('Сессия завершена')}if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('superadmin')){document.body.innerHTML='<main><div class="card critical">Недостаточно прав. Вернитесь в доступное рабочее пространство.</div></main>';return}token=s.api_token||'';businessTimeZone=s.business_timezone||'UTC';businessTimeLabel=s.business_timezone_label||businessTimeZone;timeContext.textContent='время: '+businessTimeLabel;await loadData()}
function metric(label,value,cls=''){return `<div class="metric-card"><div class="muted">${esc(label)}</div><div class="metric ${cls}">${esc(value)}</div></div>`}
function nextStep(d){if(d.status==='critical'){const failure=(d.hard_failures||[])[0]||'critical_security';next.className='next critical';next.innerHTML=`<b>Главный следующий шаг</b><div>Остановите необязательные административные изменения и разберите критическую проверку «${esc(failure)}». Не исправляйте журнал вручную и не отключайте защитные контуры ради продолжения работы.</div>`;return}if(d.status==='attention'){next.className='next warning';next.innerHTML='<b>Главный следующий шаг</b><div>Разберите критические/предупреждающие события и активные блокировки, начиная с самых свежих. Если событие ожидаемое — подтвердите его по связанному аудиту.</div>';return}next.className='next';next.innerHTML='<b>Главный следующий шаг</b><div class="ok">Критических действий сейчас не требуется. Продолжайте обычный контроль и проверяйте новые события по мере появления.</div>'}
async function loadData(){status.textContent='Обновляем…';try{const d=await api('/security-events/status?hours=24&limit=200');businessTimeZone=d.business_timezone||businessTimeZone;businessTimeLabel=d.business_timezone_label||businessTimeLabel;timeContext.textContent='время: '+businessTimeLabel;freshness.textContent='Обновлено '+dt(d.generated_at)+' · окно '+esc(d.window_hours)+' ч';summary.innerHTML=metric('Статус',d.status,d.status==='critical'?'critical':d.status==='attention'?'warning':'ok')+metric('Критические события',d.counts.critical,'critical')+metric('Предупреждения',d.counts.warning,'warning')+metric('Активные блокировки',d.active_login_locks);nextStep(d);checks.innerHTML=`<b class="${d.audit_integrity.ok?'ok':'critical'}">Аудит: ${d.audit_integrity.ok?'целостен':'НАРУШЕН'}</b><br><b class="${d.security_keys.ok?'ok':'critical'}">Ключи: ${d.security_keys.ok?'готовы':'требуют настройки'}</b><br>Суперадминистраторы без MFA: ${esc(d.superadmins_without_mfa.length)}<br>Активные отозванные сессии: ${esc(d.active_revoked_sessions)}${d.hard_failures.length?`<pre>${esc(JSON.stringify(d.hard_failures,null,2))}</pre>`:''}`;events.innerHTML=d.items.length?d.items.map(e=>`<div class="event"><b class="${esc(e.severity)}">${esc(e.severity.toUpperCase())}</b> · <b>${esc(e.summary)}</b><br><span class="muted">#${esc(e.chain_sequence)} · ${esc(e.action)} · ${esc(dt(e.created_at))}</span>${Object.keys(e.details||{}).length?`<pre>${esc(JSON.stringify(e.details,null,2))}</pre>`:''}</div>`).join(''):'Событий за выбранный период нет.';status.textContent='Проверка обновлена.'}catch(error){summary.innerHTML=metric('Статус','ошибка','critical');next.className='next critical';next.innerHTML='<b>Главный следующий шаг</b><div>Повторите проверку. Если контроль безопасности остаётся недоступен, не выполняйте изменения доступа, MFA или ключей вслепую.</div>';checks.innerHTML='<span class="critical">'+esc(error.message)+'</span>';events.textContent='';status.textContent=error.message}}
boot();
</script>
</main>
</body>
</html>
"""