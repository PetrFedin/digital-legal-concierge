from __future__ import annotations

from pathlib import Path
import shutil
import sys

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(prefix="/diagnostic-center", tags=["diagnostic-center"])


def _request_token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_diagnostic_admin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await resolve_document_actor(db, _request_token(request, header_token))
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("")
async def diagnostic_center(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_diagnostic_admin(request, db, x_admin_token)
    storage = Path(settings.storage_dir)
    backups = Path(settings.backup_dir)
    disk = shutil.disk_usage(storage if storage.exists() else ".")
    return {
        "version": "1.0.0-v46",
        "python": sys.version.split()[0],
        "run_bot": bool(settings.run_bot),
        "run_scheduler": bool(settings.run_scheduler),
        "payment_provider": settings.payment_provider,
        "storage_ready": storage.exists(),
        "storage_files": len(list(storage.rglob("*"))) if storage.exists() else 0,
        "backups_ready": backups.exists(),
        "backup_files": len(list(backups.glob("*"))) if backups.exists() else 0,
        "free_disk_mb": int(disk.free / 1024 / 1024),
        "pages": [
            "/health-center/ui",
            "/recovery-center/ui",
            "/admin/workdesk/ui",
            "/admin-ui",
        ],
    }


@router.get("/ui", response_class=HTMLResponse)
async def diagnostic_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _require_diagnostic_admin(request, db, x_admin_token)
    except (DocumentAccessError, HTTPException) as exc:
        if exc.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if exc.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    return HTMLResponse(
        """
<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Диагностика — Digital Legal Concierge</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f4f6fa;color:#172033}.wrap{max-width:960px;margin:auto;padding:28px}.head{display:flex;justify-content:space-between;gap:12px;align-items:center}.card{background:#fff;padding:18px;border:1px solid #e4e7ec;border-radius:14px;margin:12px 0;box-shadow:0 8px 24px rgba(16,24,40,.06)}pre{white-space:pre-wrap;overflow:auto}.muted{color:#667085}a{color:#3157d5;text-decoration:none;font-weight:700}
</style></head><body><div class='wrap'><div class='head'><div><h1>Диагностика</h1><div class='muted'>Без секретов конфигурации и внутренних путей.</div></div><a href='/admin/workdesk/ui'>К рабочему столу</a></div><div id='out' class='card'>Проверяю состояние…</div></div>
<script>fetch('/diagnostic-center',{credentials:'same-origin',cache:'no-store'}).then(async r=>{if(r.status===401){location.href='/login';return}if(r.status===403){location.href='/admin-ui';return}const d=await r.json();if(!r.ok)throw Error(d.detail||'Ошибка диагностики');document.getElementById('out').innerHTML='<pre>'+JSON.stringify(d,null,2)+'</pre>'}).catch(e=>{document.getElementById('out').textContent='Не удалось загрузить диагностику: '+e.message})</script></body></html>
"""
    )
