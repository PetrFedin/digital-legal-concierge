from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.api.security_event_center import require_security_superadmin
from app.config import settings
from app.security.backup_encryption import (
    BACKUP_SUFFIX,
    BackupSecurityError,
    inspect_encrypted_backup,
    verify_encrypted_backup,
)
from app.security.security_events import record_security_event_best_effort

router = APIRouter(tags=["backup-center"])
ARCHIVE_NAME_RE = re.compile(
    r"^legal_concierge_[0-9]{8}_[0-9]{6}(?:_[0-9]+)?\.dlcbak$"
)


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


def _require_backup_admin(request: Request, header_token: str | None) -> dict:
    return require_security_superadmin(_token(request, header_token))


def _safe_backup_root() -> Path:
    root = Path(settings.backup_dir)
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise BackupSecurityError("Каталог резервных копий имеет небезопасный тип")
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def _safe_archive(root: Path, archive_name: str) -> Path:
    if not ARCHIVE_NAME_RE.fullmatch(str(archive_name or "")):
        raise HTTPException(400, "Некорректное имя резервной копии")
    candidate = root / archive_name
    if candidate.is_symlink() or not candidate.is_file():
        raise HTTPException(404, "Резервная копия не найдена")
    return candidate


def backup_inventory() -> dict:
    try:
        root = _safe_backup_root()
    except BackupSecurityError:
        return {
            "ok": False,
            "backup_dir_exists": False,
            "backup_dir_secure": False,
            "encrypted_backups_count": 0,
            "insecure_legacy_backups_count": 0,
            "unreadable_encrypted_backups_count": 0,
            "unsafe_archive_names_count": 0,
            "latest": [],
            "database_url_configured": bool(settings.database_url),
            "storage_exists": Path(settings.storage_dir).exists(),
            "encryption_key_id": settings.backup_encryption_key_id,
            "max_backup_mb": settings.max_backup_mb,
            "retention_days": settings.backup_retention_days,
            "secrets_included": False,
            "restore_mode": "verified_staging_only",
        }
    all_encrypted = [
        path
        for path in root.glob(f"*{BACKUP_SUFFIX}")
        if path.is_file() and not path.is_symlink()
    ]
    unsafe_names = [path for path in all_encrypted if not ARCHIVE_NAME_RE.fullmatch(path.name)]
    encrypted = sorted(
        [path for path in all_encrypted if ARCHIVE_NAME_RE.fullmatch(path.name)],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    insecure = sorted(
        [
            path
            for path in root.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and not path.name.endswith(BACKUP_SUFFIX)
            and path.suffix.lower() in {".gz", ".zip", ".db", ".sqlite", ".sqlite3"}
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    latest: list[dict] = []
    unreadable = len(unsafe_names)
    for path in encrypted[:10]:
        try:
            metadata = inspect_encrypted_backup(path)
            latest.append(
                {
                    "name": path.name,
                    "encrypted_size": path.stat().st_size,
                    "key_id": metadata.key_id,
                    "created_at": metadata.created_at,
                    "plaintext_size": metadata.plaintext_size,
                    "format_version": metadata.format_version,
                    "verified_now": False,
                }
            )
        except BackupSecurityError:
            unreadable += 1
            latest.append(
                {
                    "name": path.name,
                    "encrypted_size": path.stat().st_size,
                    "recognized": False,
                    "verified_now": False,
                }
            )
    return {
        "ok": unreadable == 0 and len(insecure) == 0,
        "backup_dir_exists": root.exists(),
        "backup_dir_secure": True,
        "encrypted_backups_count": len(encrypted),
        "insecure_legacy_backups_count": len(insecure),
        "unreadable_encrypted_backups_count": unreadable,
        "unsafe_archive_names_count": len(unsafe_names),
        "latest": latest,
        "database_url_configured": bool(settings.database_url),
        "storage_exists": Path(settings.storage_dir).exists(),
        "encryption_key_id": settings.backup_encryption_key_id,
        "max_backup_mb": settings.max_backup_mb,
        "retention_days": settings.backup_retention_days,
        "secrets_included": False,
        "restore_mode": "verified_staging_only",
    }


@router.get("/backup-center/ui", response_class=HTMLResponse)
async def backup_center_ui():
    return HTMLResponse(BACKUP_CENTER_HTML)


@router.get("/backup-center/status")
async def backup_center_status(
    request: Request,
    x_admin_token: str | None = Header(default=None),
):
    _require_backup_admin(request, x_admin_token)
    result = backup_inventory()
    result["version"] = "1.0.0-v40"
    return result


@router.post("/backup-center/verify/{archive_name}")
async def verify_backup_archive(
    archive_name: str,
    request: Request,
    x_admin_token: str | None = Header(default=None),
):
    actor = _require_backup_admin(request, x_admin_token)
    try:
        root = _safe_backup_root()
    except BackupSecurityError as error:
        raise HTTPException(409, "Каталог резервных копий небезопасен") from error
    candidate = _safe_archive(root, archive_name)
    try:
        metadata = verify_encrypted_backup(candidate)
        return {
            "ok": True,
            "archive": archive_name,
            **asdict(metadata),
        }
    except BackupSecurityError as error:
        await record_security_event_best_effort(
            action="security.backup_verification_failed",
            severity="critical",
            source="backup_center",
            actor_id=int(actor.get("uid") or 0) or None,
            client_address=request.client.host if request.client else None,
            resource_type="backup",
            details={"reason": type(error).__name__, "archive": archive_name},
            comment="Резервная копия не прошла криптографическую проверку",
            sample_seconds=1,
        )
        raise HTTPException(409, "Резервная копия не прошла проверку целостности") from error


BACKUP_CENTER_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backup Center</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}header{background:#111827;color:white;padding:22px}main{max-width:1100px;margin:auto;padding:22px;display:grid;gap:16px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}.ok{color:#166534;font-weight:700}.bad{color:#991b1b;font-weight:700}.muted{font-size:13px;color:#6b7280}button,a.button{display:inline-block;border:0;padding:9px 12px;border-radius:9px;background:#2563eb;color:#fff;text-decoration:none;font-weight:700;cursor:pointer}button:disabled{opacity:.5}table{width:100%;border-collapse:collapse}td,th{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left}code,pre{background:#0b1020;color:#d1e7ff;border-radius:10px;padding:10px;display:block;overflow:auto}@media(max-width:800px){.grid{grid-template-columns:1fr}table{display:block;overflow:auto}}
</style>
</head>
<body>
<header><h1>💾 Зашифрованные резервные копии</h1><p>AES-GCM, manifest SHA-256, проверенное восстановление только в staging.</p></header>
<main>
<section id="summary" class="grid"><div class="card">Загрузка…</div></section>
<section class="card"><h2>Последние копии</h2><div id="files"></div><div id="message" class="muted"></div></section>
<section class="card"><h2>Команды</h2><pre>./backup.sh
python -m app.security.backup_cli verify backups/имя.dlcbak
./restore.sh backups/имя.dlcbak /пустой/staging-каталог</pre><p class="muted">`.env` и ключи намеренно не входят в архив. Их необходимо хранить отдельно в secret manager.</p></section>
<section class="card"><a class="button" href="/security-events/ui">Security Center</a> <a class="button" href="/launch-check">Launch check</a></section>
</main>
<script>
let token='';
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function size(v){if(v===null||v===undefined)return '—';if(v<1024)return v+' Б';if(v<1048576)return (v/1024).toFixed(1)+' КБ';return (v/1048576).toFixed(1)+' МБ'}
async function api(path,opts={}){const response=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||'Ошибка');return data}
async function boot(){const session=await fetch('/auth/session');if(!session.ok){location.href='/login';return}const data=await session.json();if(!(data.roles||[data.role]).includes('superadmin')||!data.mfa){document.body.innerHTML='<main><div class="card bad">Требуется MFA-сессия суперадминистратора.</div></main>';return}token=data.api_token;await load()}
async function load(){try{const data=await api('/backup-center/status');summary.innerHTML=`<div class="card"><b>Зашифрованные</b><p class="ok">${esc(data.encrypted_backups_count)}</p></div><div class="card"><b>Небезопасные старые</b><p class="${data.insecure_legacy_backups_count?'bad':'ok'}">${esc(data.insecure_legacy_backups_count)}</p></div><div class="card"><b>Нечитаемые</b><p class="${data.unreadable_encrypted_backups_count?'bad':'ok'}">${esc(data.unreadable_encrypted_backups_count)}</p></div>`;files.innerHTML=data.latest.length?`<table><tr><th>Файл</th><th>Ключ / дата</th><th>Размер</th><th></th></tr>${data.latest.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.key_id||'не распознан')}<br><span class="muted">${esc(x.created_at||'')}</span></td><td>${esc(size(x.encrypted_size))}</td><td><button data-archive="${esc(x.name)}">Полная проверка</button></td></tr>`).join('')}</table>`:'Копий пока нет.';files.querySelectorAll('button[data-archive]').forEach(button=>button.addEventListener('click',()=>verifyArchive(button.dataset.archive,button)))}catch(error){message.className='bad';message.textContent=error.message}}
async function verifyArchive(name,button){button.disabled=true;message.className='muted';message.textContent='Расшифрование и проверка manifest…';try{const data=await api('/backup-center/verify/'+encodeURIComponent(name),{method:'POST',body:'{}'});message.className='ok';message.textContent='Проверка успешна: '+data.plaintext_sha256.slice(0,16)+'…'}catch(error){message.className='bad';message.textContent=error.message}finally{button.disabled=false}}
boot();
</script>
</body>
</html>
"""
