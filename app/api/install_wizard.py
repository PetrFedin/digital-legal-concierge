from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.config import settings

router = APIRouter(prefix="/install-wizard", tags=["install-wizard"])

@router.get("")
async def install_wizard_status():
    steps = [
        {"step": 1, "title": "BOT_TOKEN", "ok": bool(settings.bot_token and settings.bot_token != 'CHANGE_ME') or not settings.run_bot},
        {"step": 2, "title": "База данных", "ok": bool(settings.database_url)},
        {"step": 3, "title": "Админ", "ok": bool(settings.admin_password or settings.app_env == 'local')},
        {"step": 4, "title": "Платежи", "ok": settings.payment_provider == 'fake' or bool(settings.yookassa_shop_id and settings.yookassa_secret_key)},
        {"step": 5, "title": "Хранилище", "ok": Path(settings.storage_dir).exists()},
    ]
    return {"ok": all(s['ok'] for s in steps), "steps": steps, "next": "./bot-control.sh setup или scripts/production_wizard.py"}

@router.get("/ui", response_class=HTMLResponse)
async def install_wizard_ui():
    return """
    <html><head><meta charset='utf-8'><title>Install Wizard</title><style>body{font-family:Arial;margin:30px;background:#f7f7f7}.card{background:white;padding:18px;border-radius:14px;margin:12px 0}</style></head>
    <body><h1>Install Wizard v20</h1><div class='card'>Показывает, что осталось настроить для запуска.</div><div id='steps'></div>
    <script>fetch('/install-wizard').then(r=>r.json()).then(d=>{let html='';d.steps.forEach(s=>html+=`<div class='card'>${s.ok?'✅':'❌'} Шаг ${s.step}: ${s.title}</div>`);document.getElementById('steps').innerHTML=html})</script></body></html>
    """
