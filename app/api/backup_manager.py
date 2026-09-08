from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.backup_center import (
    _safe_archive,
    _safe_backup_root,
    _verify_restorable_archive,
    backup_inventory,
)
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_SUPERADMIN
from app.security.backup_encryption import BackupSecurityError
from app.security.backup_restore_assessment import BackupRevokedError
from app.security.backup_restore_fence import BackupRestoreFenceError
from app.security.document_access import DocumentAccessError, resolve_document_actor
from app.security.security_events import record_security_event_best_effort

router = APIRouter(tags=["backup-manager"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_superadmin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(status_code=403, detail="Доступ только для суперадминистратора")
    return actor


async def _status_payload() -> dict:
    result = await asyncio.to_thread(backup_inventory)
    result["version"] = "1.0.0-v46"
    result.pop("database_url_configured", None)
    result.pop("storage_exists", None)
    result.pop("encryption_key_id", None)
    return result


@router.get("/backup-center/status")
@router.get("/backup-manager/status")
async def backup_status_override(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_superadmin(request, db, x_admin_token)
    return await _status_payload()


@router.post("/backup-center/verify/{archive_name}")
async def verify_backup_override(
    archive_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _require_superadmin(request, db, x_admin_token)
    try:
        root = _safe_backup_root()
        candidate = _safe_archive(root, archive_name)
        metadata = await asyncio.to_thread(_verify_restorable_archive, candidate, root)
        return {"ok": True, "archive": archive_name, **asdict(metadata)}
    except BackupRevokedError as error:
        action = "security.backup_restore_revoked"
        detail = "Резервная копия отозвана и недоступна для восстановления"
    except BackupRestoreFenceError as error:
        action = "security.backup_restore_policy_unavailable"
        detail = "Политика восстановления недоступна"
    except BackupSecurityError as error:
        action = "security.backup_verification_failed"
        detail = "Резервная копия не прошла проверку целостности"
    await record_security_event_best_effort(
        action=action,
        severity="critical",
        source="backup_manager_override",
        actor_id=int(actor.account_id),
        client_address=request.client.host if request.client else None,
        resource_type="backup",
        details={"reason": type(error).__name__, "archive": archive_name},
        comment=detail,
        sample_seconds=1,
    )
    raise HTTPException(status_code=409, detail=detail) from error


@router.get("/backup-manager/ui", response_class=HTMLResponse)
@router.get("/backup-center/ui", response_class=HTMLResponse)
async def backup_ui_override(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _require_superadmin(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return HTMLResponse(BACKUP_UI)


BACKUP_UI = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Резервные копии</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f4f6fa;color:#172033}header{background:#111827;color:#fff;padding:20px 24px}.wrap{max-width:1040px;margin:auto;padding:22px}.head{display:flex;justify-content:space-between;gap:12px;align-items:center}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.card{background:#fff;border:1px solid #e4e7ec;border-radius:14px;padding:14px;margin-top:12px}.ok{color:#067647}.bad{color:#b42318}.muted{color:#667085;font-size:13px}button,a.button{border:0;border-radius:9px;padding:9px 11px;background:#3157d5;color:#fff;text-decoration:none;font-weight:750;cursor:pointer;display:inline-block}button:disabled{opacity:.5;cursor:not-allowed}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #e4e7ec}@media(max-width:760px){.grid{grid-template-columns:1fr 1fr}table{display:block;overflow:auto}}
</style></head><body><header><div class="head" style="max-width:1040px;margin:auto"><div><h1>💾 Резервные копии</h1><div class="muted" style="color:#d0d5dd">MFA-superadmin · encrypted archives · restore-fence</div></div><a class="button" href="/security-events/ui">Security Center</a></div></header><div class="wrap"><div id="summary" class="grid"><div class="card">Проверяю состояние…</div></div><div class="card"><div class="head"><h2>Последние архивы</h2><button onclick="load()">Обновить</button></div><div id="files"></div><div id="message" class="muted"></div></div></div><script>
let token='';const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const size=v=>v==null?'—':v<1024?v+' Б':v<1048576?(v/1024).toFixed(1)+' КБ':(v/1048576).toFixed(1)+' МБ';async function api(path,opt={}){const r=await fetch(path,{credentials:'same-origin',cache:'no-store',...opt,headers:{'x-admin-token':token,'content-type':'application/json',...(opt.headers||{})}});const d=await r.json().catch(()=>({}));if(r.status===401){location.href='/login';throw Error('Требуется вход')}if(!r.ok)throw Error(d.detail||'Ошибка');return d}async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('superadmin')||!s.mfa_verified){document.body.innerHTML='<div class="wrap"><div class="card bad">Требуется активная MFA-сессия суперадминистратора.</div></div>';return}token=s.api_token||'';await load()}async function load(){try{message.textContent='';const d=await api('/backup-center/status');summary.innerHTML=`<div class="card"><b>Доступны</b><h2 class="ok">${d.restorable_encrypted_backups_count||0}</h2></div><div class="card"><b>Отозваны</b><h2 class="${d.revoked_encrypted_backups_count?'bad':'ok'}">${d.revoked_encrypted_backups_count||0}</h2></div><div class="card"><b>Нечитаемые/старые</b><h2 class="${(d.unreadable_encrypted_backups_count||0)+(d.insecure_legacy_backups_count||0)?'bad':'ok'}">${(d.unreadable_encrypted_backups_count||0)+(d.insecure_legacy_backups_count||0)}</h2></div><div class="card"><b>Restore-fence</b><h2 class="${d.restore_fence_valid?'ok':'bad'}">${d.restore_fence_valid?'OK':'BLOCK'}</h2></div>`;files.innerHTML=(d.latest||[]).length?`<table><tr><th>Архив</th><th>Создан</th><th>Размер</th><th>Статус</th><th>Действие</th></tr>${d.latest.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.created_at||'—')}</td><td>${esc(size(x.encrypted_size))}</td><td>${x.revoked?'Отозван':x.restorable?'Доступен':'Заблокирован'}</td><td><button ${x.restorable?'':'disabled'} data-name="${esc(x.name)}">Проверить</button></td></tr>`).join('')}</table>`:'Архивов пока нет.';files.querySelectorAll('button[data-name]:not([disabled])').forEach(b=>b.addEventListener('click',()=>verifyArchive(b.dataset.name,b)))}catch(e){message.className='bad';message.textContent=e.message}}async function verifyArchive(name,button){button.disabled=true;message.className='muted';message.textContent='Проверяю restore-fence и целостность архива…';try{const d=await api('/backup-center/verify/'+encodeURIComponent(name),{method:'POST',body:'{}'});message.className='ok';message.textContent='Архив проверен: '+String(d.plaintext_sha256||'').slice(0,16)+'…'}catch(e){message.className='bad';message.textContent=e.message}finally{button.disabled=false}}boot();
</script></body></html>
"""
