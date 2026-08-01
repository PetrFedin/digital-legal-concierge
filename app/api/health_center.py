from pathlib import Path
import shutil
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db

router = APIRouter(prefix="/health-center", tags=["health-center"])


def status(ok: bool, title: str, details: str = "") -> dict:
    return {"ok": ok, "title": title, "details": details, "state": "green" if ok else "red"}


@router.get("")
async def health_center(db: AsyncSession = Depends(get_db)):
    checks = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = status(True, "База данных", "Соединение активно")
    except Exception as exc:
        checks["database"] = status(False, "База данных", str(exc))

    storage_path = Path(settings.storage_dir)
    checks["storage"] = status(storage_path.exists(), "Хранилище документов", str(storage_path))

    backup_path = Path(settings.backup_dir)
    checks["backups"] = status(backup_path.exists(), "Резервные копии", str(backup_path))

    disk = shutil.disk_usage(".")
    free_mb = int(disk.free / 1024 / 1024)
    checks["disk"] = status(free_mb >= settings.min_free_disk_mb, "Свободное место", f"{free_mb} MB")

    bot_ok = (not settings.run_bot) or bool(settings.bot_token and settings.bot_token != "CHANGE_ME")
    checks["telegram"] = status(bot_ok, "Telegram", "BOT_TOKEN задан" if bot_ok else "BOT_TOKEN не задан")

    payment_ok = settings.payment_provider == "fake" or bool(settings.yookassa_shop_id and settings.yookassa_secret_key)
    checks["payments"] = status(payment_ok, "Платежи", settings.payment_provider)

    checks["scheduler"] = status(True, "Scheduler", "включен" if settings.run_scheduler else "выключен")
    checks["demo_mode"] = status(True, "Demo Mode", "включен" if settings.demo_mode else "выключен")

    ok = all(item["ok"] for item in checks.values())
    return {"ok": ok, "version": "1.0.0-v20", "checks": checks}


@router.get("/ui", response_class=HTMLResponse)
async def health_center_ui():
    return """
    <html><head><meta charset='utf-8'><title>Health Center</title>
    <style>body{font-family:Arial;margin:30px;background:#f7f7f7} .card{background:white;padding:20px;border-radius:14px;margin:12px 0;box-shadow:0 1px 8px #ddd} a{display:inline-block;margin:6px 10px 6px 0}</style></head>
    <body><h1>Health Center v20</h1><div class='card'>Проверка состояния Telegram-бота, БД, файлов, платежей, backup и scheduler.</div>
    <a href='/health-center'>JSON status</a><a href='/diagnostic-center/ui'>Diagnostic Center</a><a href='/recovery-center/ui'>Recovery Center</a><a href='/operator'>Operator</a><a href='/admin-ui'>Admin</a>
    <script>fetch('/health-center').then(r=>r.json()).then(d=>{let html=''; for(const [k,v] of Object.entries(d.checks)){html+=`<div class="card"><b>${v.state==='green'?'🟢':'🔴'} ${v.title}</b><br>${v.details}</div>`} document.body.insertAdjacentHTML('beforeend',html)})</script>
    </body></html>
    """
