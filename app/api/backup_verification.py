from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse

from app.domain.backups.verification_service import (
    BackupVerificationError,
    BackupVerificationService,
)
from app.security.access_control import ROLE_ADMIN, decode_access_token, has_role

router = APIRouter(
    prefix="/admin/backup-verification",
    tags=["admin", "backup-verification"],
)


def require_admin(token: str | None) -> dict:
    payload = decode_access_token(token)
    if not payload or not has_role(payload.get("roles"), ROLE_ADMIN):
        raise HTTPException(
            status_code=403,
            detail="Доступ только для администратора",
        )
    return payload


@router.get("")
async def list_verified_backups(
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    return BackupVerificationService().list_backups()


@router.post("/create")
async def create_verified_backup(
    payload: dict | None = None,
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    try:
        return await BackupVerificationService().create_verified_backup(
            label=(payload or {}).get("label"),
        )
    except BackupVerificationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{filename}/verify")
async def verify_backup(
    filename: str,
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    try:
        return await BackupVerificationService().verify_backup(filename)
    except BackupVerificationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{filename}/stage")
async def stage_restore(
    filename: str,
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    try:
        return await BackupVerificationService().stage_restore(filename)
    except BackupVerificationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/ui", response_class=HTMLResponse)
async def backup_verification_ui():
    return HTMLResponse(BACKUP_VERIFICATION_HTML)


BACKUP_VERIFICATION_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка резервных копий</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between}main{max-width:1400px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}.notice{background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:12px;margin-bottom:16px}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}button{border:0;border-radius:9px;padding:8px 11px;color:#fff;background:#2563eb;font-weight:700;cursor:pointer}.green{background:#15803d}.yellow{background:#ca8a04}.muted{font-size:13px;color:#6b7280}.badge{display:inline-block;border-radius:999px;padding:4px 8px;background:#e5e7eb;font-size:12px}.ok{background:#dcfce7;color:#166534}.actions{display:flex;gap:6px;flex-wrap:wrap}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}@media(max-width:900px){table{display:block;overflow-x:auto;font-size:12px}}
</style>
</head>
<body>
<header><b>⚖ Проверка резервных копий</b><a href="/admin-ui" style="color:white">Админка</a></header>
<main><div class="notice"><b>Важно.</b> Копия считается пригодной только после SQLite integrity check, применения всех Alembic-миграций на временной копии и smoke-проверки обязательных таблиц. «Подготовить восстановление» создаёт отдельную staging-базу и не заменяет рабочий файл.</div><div class="card"><div class="toolbar"><button class="green" onclick="createBackup()">Создать проверенную копию</button><button onclick="load()">Обновить список</button></div><div id="content">Загрузка…</div></div><div id="message" class="muted"></div><pre id="result"></pre></main>
<script>
let token='';
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function dt(v){return v?new Date(v).toLocaleString('ru-RU'):'—'}
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;await load()}
async function load(){const rows=await api('/admin/backup-verification');content.innerHTML=rows.length?`<table><tr><th>Файл</th><th>Размер</th><th>Проверка</th><th>Ревизия</th><th>Действия</th></tr>${rows.map(x=>`<tr><td><b>${esc(x.filename)}</b><br><span class="muted">${esc(dt(x.modified_at))}</span></td><td>${Math.round(x.size/1024).toLocaleString('ru-RU')} КБ</td><td><span class="badge ${x.verified?'ok':''}">${x.verified?'Проверена':'Не проверена'}</span><br><span class="muted">${esc(dt(x.verified_at))}<br>${esc(x.sha256||'')}</span></td><td>${esc(x.alembic_revision||'—')}</td><td><div class="actions"><button onclick="verifyBackup('${esc(x.filename)}')">Проверить</button><button class="yellow" onclick="stageRestore('${esc(x.filename)}')">Подготовить восстановление</button></div></td></tr>`).join('')}</table>`:'Резервных копий нет.'}
async function createBackup(){const label=prompt('Метка копии:','manual');if(label===null)return;try{const data=await api('/admin/backup-verification/create',{method:'POST',body:JSON.stringify({label})});result.textContent=JSON.stringify(data,null,2);message.textContent='Проверенная копия создана';await load()}catch(e){message.textContent=e.message}}
async function verifyBackup(name){try{const data=await api('/admin/backup-verification/'+encodeURIComponent(name)+'/verify',{method:'POST'});result.textContent=JSON.stringify(data,null,2);message.textContent='Копия прошла полную проверку';await load()}catch(e){message.textContent=e.message}}
async function stageRestore(name){if(!confirm('Создать отдельную staging-базу для восстановления? Рабочая база не будет заменена.'))return;try{const data=await api('/admin/backup-verification/'+encodeURIComponent(name)+'/stage',{method:'POST'});result.textContent=JSON.stringify(data,null,2);message.textContent='Staging-восстановление подготовлено и проверено'}catch(e){message.textContent=e.message}}
boot();
</script>
</body>
</html>
"""
