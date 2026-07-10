from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings

router = APIRouter(tags=["go-live-center"])


GO_LIVE_ITEMS = [
    ("01_env", "Файл .env создан", lambda: Path(".env").exists()),
    ("02_bot_token", "BOT_TOKEN заполнен", lambda: bool(settings.bot_token and settings.bot_token != "CHANGE_ME")),
    ("03_admin_login", "Логин/пароль администратора заданы", lambda: bool(settings.admin_username and settings.admin_password)),
    ("04_db", "DATABASE_URL задан", lambda: bool(settings.database_url)),
    ("05_storage", "Папка storage существует", lambda: Path(settings.storage_dir).exists()),
    ("06_run", "Есть простой запуск ./run.sh", lambda: Path("run.sh").exists()),
    ("07_control", "Есть меню управления ./bot-control.sh", lambda: Path("bot-control.sh").exists()),
    ("08_acceptance", "Есть acceptance.sh", lambda: Path("acceptance.sh").exists()),
    ("09_docker", "Есть Dockerfile", lambda: Path("Dockerfile").exists()),
    ("10_compose", "Есть docker-compose.yml", lambda: Path("docker-compose.yml").exists()),
    ("11_prod_compose", "Есть production compose", lambda: Path("docker-compose.production.yml").exists()),
    ("12_nginx", "Есть nginx шаблон", lambda: Path("deploy/nginx/legal-concierge-bot.conf").exists()),
    ("13_systemd", "Есть systemd шаблон", lambda: Path("deploy/systemd/legal-concierge-bot.service").exists()),
    ("14_docs", "Есть финальная инструкция", lambda: Path("docs/START_HERE_FINAL_V27.md").exists()),
]


def build_go_live_status() -> dict:
    checks = []
    for code, title, fn in GO_LIVE_ITEMS:
        try:
            ok = bool(fn())
        except Exception:
            ok = False
        checks.append({"code": code, "title": title, "ok": ok})
    done = sum(1 for item in checks if item["ok"])
    return {
        "version": "v27-go-live",
        "ready": done == len(checks),
        "score": round(done / len(checks) * 100, 1),
        "checks": checks,
        "operator_urls": {
            "Главная оператора": "/operator",
            "Админка": "/admin-ui",
            "Настройки": "/settings-ui",
            "Производственный центр": "/production-center/ui",
            "Health Center": "/health-center/ui",
            "Диагностика": "/diagnostic-center/ui",
            "Recovery": "/recovery-center/ui",
            "Поиск": "/search-center/ui",
            "Acceptance": "/acceptance-center/ui",
            "Go Live": "/go-live/ui",
        },
        "launch_order": [
            "1. Распаковать архив",
            "2. Перейти в папку tg_ready_v27",
            "3. Запустить ./run.sh",
            "4. Открыть http://localhost:8000/go-live/ui",
            "5. Открыть http://localhost:8000/admin-ui",
            "6. Проверить Telegram-бота командой /start",
        ],
    }


@router.get("/go-live/status")
async def go_live_status():
    return build_go_live_status()


@router.get("/go-live/ui", response_class=HTMLResponse)
async def go_live_ui():
    data = build_go_live_status()
    rows = "".join(
        f"<tr><td>{item['code']}</td><td>{item['title']}</td><td>{'✅' if item['ok'] else '❌'}</td></tr>"
        for item in data["checks"]
    )
    links = "".join(
        f"<a href='{url}' class='card'>{name}</a>" for name, url in data["operator_urls"].items()
    )
    launch_order = "".join(f"<li>{step}</li>" for step in data["launch_order"])
    badge_cls = "ok" if data["ready"] else "warn"
    badge_text = "ГОТОВО К ЗАПУСКУ" if data["ready"] else "ЕСТЬ ЧТО ПРОВЕРИТЬ"
    return f"""
    <html><head><meta charset='utf-8'><title>Go Live Center</title>
    <style>
      body {{ font-family: Arial, sans-serif; background:#f5f6f8; color:#111; margin:32px; }}
      h1 {{ margin-bottom: 6px; }}
      .badge {{ display:inline-block; padding:10px 14px; border-radius:12px; font-weight:700; }}
      .ok {{ background:#d8f5df; color:#0c6b23; }} .warn {{ background:#fff0bc; color:#785400; }}
      .grid {{ display:grid; grid-template-columns: repeat(auto-fit,minmax(190px,1fr)); gap:12px; margin:20px 0; }}
      .card {{ display:block; background:#fff; border:1px solid #ddd; border-radius:12px; padding:14px; color:#111; text-decoration:none; }}
      table {{ border-collapse: collapse; background:#fff; width:100%; margin-top:16px; }}
      th,td {{ border:1px solid #ddd; padding:10px; text-align:left; }}
      code {{ background:#111; color:#fff; border-radius:6px; padding:4px 7px; }}
    </style></head><body>
      <h1>Go Live Center v27</h1>
      <div class='badge {badge_cls}'>{badge_text}: {data['score']}%</div>
      <p>Финальный экран перед запуском: показывает, что связано, что готово и куда идти оператору.</p>
      <h2>Порядок запуска</h2><ol>{launch_order}</ol>
      <h2>Рабочие разделы</h2><div class='grid'>{links}</div>
      <h2>Чек-лист готовности</h2><table><tr><th>Код</th><th>Проверка</th><th>Статус</th></tr>{rows}</table>
      <h2>JSON</h2><p><a href='/go-live/status'>/go-live/status</a></p>
    </body></html>
    """
