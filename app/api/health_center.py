from __future__ import annotations

from pathlib import Path
import shutil

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(prefix="/health-center", tags=["health-center"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_admin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


def _check(ok: bool, title: str, details: str) -> dict:
    return {"ok": bool(ok), "title": title, "details": details}


@router.get("")
async def health_center(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    checks: dict[str, dict] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = _check(True, "База данных", "Соединение активно")
    except Exception:
        checks["database"] = _check(False, "База данных", "Соединение недоступно")

    storage = Path(settings.storage_dir)
    backups = Path(settings.backup_dir)
    checks["storage"] = _check(
        storage.exists(),
        "Хранилище документов",
        "Доступно" if storage.exists() else "Недоступно",
    )
    checks["backups"] = _check(
        backups.exists(),
        "Резервные копии",
        "Доступны" if backups.exists() else "Недоступны",
    )
    disk = shutil.disk_usage(storage if storage.exists() else ".")
    free_mb = int(disk.free / 1024 / 1024)
    checks["disk"] = _check(
        free_mb >= settings.min_free_disk_mb,
        "Свободное место",
        f"Доступно {free_mb} MB",
    )
    checks["telegram"] = _check(
        (not settings.run_bot) or bool(settings.bot_token and settings.bot_token != "CHANGE_ME"),
        "Telegram",
        "Готов" if settings.run_bot else "Отключён настройкой",
    )
    checks["scheduler"] = _check(
        True,
        "Автоматические проверки",
        "Включены" if settings.run_scheduler else "Отключены настройкой",
    )
    return {
        "ok": all(item["ok"] for item in checks.values()),
        "version": "1.0.0-v46",
        "checks": checks,
    }


@router.get("/ui", response_class=HTMLResponse)
async def health_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _require_admin(request, db, x_admin_token)
    except (DocumentAccessError, HTTPException) as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/operator", status_code=303)
        raise
    return HTMLResponse(
        """
<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Состояние системы</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f4f6fa;color:#172033}.wrap{max-width:920px;margin:auto;padding:28px}.head{display:flex;justify-content:space-between;gap:12px;align-items:center}.links{display:flex;gap:10px;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px}.card{background:#fff;padding:16px;border:1px solid #e4e7ec;border-radius:14px}.ok{color:#067647}.bad{color:#b42318}.muted{color:#667085;font-size:13px}a{color:#3157d5;text-decoration:none;font-weight:700}a:focus-visible{outline:3px solid #c7d2fe;outline-offset:3px}@media(max-width:700px){.head{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:1fr}.links{width:100%;flex-direction:column}.links a{text-align:center;padding:8px;border:1px solid #d0d5dd;border-radius:9px}}
</style></head><body><div class='wrap'><div class='head'><div><h1>Состояние системы</h1><div class='muted'>Быстрая проверка ключевых рабочих контуров.</div></div><div class='links'><a href='/operator'>Все разделы</a><a href='/admin/workdesk/ui'>К рабочему столу</a></div></div><div id='summary' class='card'>Проверяю…</div><div id='checks' class='grid'></div></div><script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));fetch('/health-center',{credentials:'same-origin',cache:'no-store'}).then(async r=>{if(r.status===401){location.href='/login';return}const d=await r.json();if(!r.ok)throw Error(d.detail||'Ошибка');summary.innerHTML=`<b class="${d.ok?'ok':'bad'}">${d.ok?'✓ Основные проверки пройдены':'⚠ Требуется внимание'}</b><div class="muted">Версия ${esc(d.version)}</div>`;checks.innerHTML=Object.values(d.checks||{}).map(x=>`<div class="card"><b class="${x.ok?'ok':'bad'}">${x.ok?'✓':'!' } ${esc(x.title)}</b><div class="muted">${esc(x.details)}</div></div>`).join('')}).catch(e=>{summary.innerHTML='<b class="bad">Проверка не выполнена</b><div class="muted">'+esc(e.message)+'</div>'})</script></body></html>
"""
    )
