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
            "/operator",
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
            return RedirectResponse(url="/operator", status_code=303)
        raise
    return HTMLResponse(
        """
<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Диагностика сервиса — Digital Legal Concierge</title><style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--blue-soft:#eef2ff;--green:#067647;--amber:#a15c00;--red:#b42318}*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:var(--bg);color:var(--ink)}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px}.head,main{max-width:1050px;margin:auto}.head{display:flex;justify-content:space-between;gap:14px;align-items:center}h1{font-size:22px;margin:0 0 4px}header p{margin:0;color:#d0d5dd;font-size:13px}.links{display:flex;gap:8px;flex-wrap:wrap}.button,button{display:inline-block;border:0;border-radius:10px;padding:9px 12px;background:var(--blue);color:#fff;text-decoration:none;font-weight:750;cursor:pointer}.secondary{background:#475467}.button:focus-visible,button:focus-visible{outline:3px solid #c7d2fe;outline-offset:2px}main{padding:20px}.card{background:var(--card);padding:16px;border:1px solid var(--line);border-radius:14px;margin-bottom:14px}.section-label{font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:7px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.metric{border:1px solid var(--line);border-radius:12px;padding:12px;background:#fff}.metric b{display:block;font-size:20px;margin-top:3px}.muted{color:var(--muted);font-size:13px;line-height:1.45}.ok{color:var(--green)}.warn{color:var(--amber)}.bad{color:var(--red)}.next{padding:12px;border:1px solid #c7d2fe;background:var(--blue-soft);border-radius:12px;line-height:1.45}.status{min-height:20px;margin-top:8px}@media(max-width:760px){.head{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:1fr 1fr}}@media(max-width:520px){main{padding:12px}.grid{grid-template-columns:1fr}.links{width:100%;flex-direction:column}.button,button{width:100%;text-align:center}}
</style></head><body><header><div class='head'><div><h1>🩺 Диагностика сервиса</h1><p>Проверка рабочих контуров без секретов конфигурации и внутренних путей.</p></div><div class='links'><a class='button secondary' href='/operator'>Все разделы</a><a class='button secondary' href='/admin/workdesk/ui'>Рабочий стол</a></div></div></header>
<main><section class='card'><div class='section-label'>Сейчас</div><div id='summary' class='grid'><div class='metric'>Проверяем состояние…</div></div><div id='next' class='next' style='margin-top:12px'><b>Главный следующий шаг</b><div class='muted'>Дождитесь завершения проверки.</div></div></section><section class='card'><div class='section-label'>Вторичные действия</div><div class='links'><button onclick='load()'>Обновить проверку</button><a class='button secondary' href='/health-center/ui'>Быстрая проверка</a><a class='button secondary' href='/admin/notification-delivery/ui'>Telegram-доставка</a></div><div id='status' class='status muted' role='status' aria-live='polite'></div></section></main>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function metric(label,value,cls=''){return `<div class="metric"><span class="muted">${esc(label)}</span><b class="${cls}">${esc(value)}</b></div>`}
function yes(v){return v?'Готов':'Требует внимания'}
async function load(){status.textContent='Обновляем…';try{const r=await fetch('/diagnostic-center',{credentials:'same-origin',cache:'no-store'});if(r.status===401){location.href='/login';return}if(r.status===403){location.href='/operator';return}const d=await r.json();if(!r.ok)throw Error(d.detail||'Ошибка диагностики');const problems=[];if(!d.storage_ready)problems.push('хранилище документов');if(!d.backups_ready)problems.push('каталог резервных копий');if(Number(d.free_disk_mb||0)<=0)problems.push('свободное место');summary.innerHTML=metric('Приложение','v'+esc(d.version||'—'),'ok')+metric('Telegram-бот',d.run_bot?'Включён':'Отключён',d.run_bot?'ok':'warn')+metric('Автоматические проверки',d.run_scheduler?'Включены':'Отключены',d.run_scheduler?'ok':'warn')+metric('Хранилище документов',yes(d.storage_ready),d.storage_ready?'ok':'bad')+metric('Резервные копии',yes(d.backups_ready),d.backups_ready?'ok':'bad')+metric('Свободное место',Number(d.free_disk_mb||0).toLocaleString('ru-RU')+' МБ',Number(d.free_disk_mb||0)>0?'ok':'bad');if(problems.length){next.innerHTML='<b>Главный следующий шаг</b><div class="bad">Проверьте: '+problems.map(esc).join(', ')+'. Не выполняйте действия, зависящие от неисправного контура, пока причина не устранена.</div>'}else{next.innerHTML='<b>Главный следующий шаг</b><div class="ok">Критических технических проблем в этой сводке не найдено. Продолжайте обычную операционную работу.</div>'}status.textContent='Проверка обновлена.'}catch(e){summary.innerHTML='<div class="metric bad">Диагностика не загружена</div>';next.innerHTML='<b>Главный следующий шаг</b><div class="bad">Повторите проверку. Если ошибка сохраняется, не меняйте статусы дел вслепую.</div>';status.textContent=e.message}}
load();
</script></body></html>
"""
    )
