from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings

router = APIRouter(tags=["production-center"])


def _check_file(path: str) -> bool:
    return Path(path).exists()


def build_production_status() -> dict:
    root = Path.cwd()
    checks = {
        "env_file": _check_file(".env"),
        "bot_token": bool(settings.bot_token and settings.bot_token != "CHANGE_ME"),
        "database_url": bool(settings.database_url),
        "storage_dir": Path(settings.storage_dir).exists(),
        "admin_credentials": bool(settings.admin_username and settings.admin_password),
        "admin_token": bool(settings.admin_api_token and settings.admin_api_token != "dev-admin-token"),
        "payment_configured": settings.payment_provider == "fake" or bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
        "run_script": _check_file("run.sh"),
        "control_script": _check_file("bot-control.sh"),
        "backup_script": _check_file("backup.sh"),
        "restore_script": _check_file("restore.sh"),
        "acceptance_script": _check_file("acceptance.sh"),
        "dockerfile": _check_file("Dockerfile"),
        "docker_compose": _check_file("docker-compose.yml"),
        "production_compose": _check_file("docker-compose.production.yml"),
        "nginx_template": _check_file("deploy/nginx/legal-concierge-bot.conf"),
        "systemd_template": _check_file("deploy/systemd/legal-concierge-bot.service"),
        "docs_start": _check_file("docs/START_SIMPLE_V27.md"),
        "docs_handover": _check_file("docs/FINAL_HANDOVER_V27.md"),
    }
    ready_count = sum(1 for value in checks.values() if value)
    total = len(checks)
    return {
        "version": "v27-production-consolidated",
        "ready": ready_count == total,
        "score": round(ready_count / total * 100, 1),
        "checks": checks,
        "important_urls": {
            "operator": "/operator",
            "admin": "/admin-ui",
            "health_center": "/health-center/ui",
            "diagnostic_center": "/diagnostic-center/ui",
            "recovery_center": "/recovery-center/ui",
            "settings": "/settings-ui",
            "search": "/search-center/ui",
            "acceptance": "/acceptance-center/ui",
            "production": "/production-center/ui",
        },
        "launch_commands": [
            "./run.sh",
            "./bot-control.sh",
            "./acceptance.sh",
            "docker compose -f docker-compose.production.yml up -d",
        ],
    }


@router.get("/production-center/status")
async def production_status():
    return build_production_status()


@router.get("/production-center/ui", response_class=HTMLResponse)
async def production_ui():
    status = build_production_status()
    rows = "".join(
        f"<tr><td>{key}</td><td>{'✅' if value else '❌'}</td></tr>"
        for key, value in status["checks"].items()
    )
    links = "".join(
        f"<a class='card' href='{url}'>{name}</a>"
        for name, url in status["important_urls"].items()
    )
    commands = "<br>".join(f"<code>{cmd}</code>" for cmd in status["launch_commands"])
    badge = "ready" if status["ready"] else "warn"
    title = "Готово к запуску" if status["ready"] else "Нужно завершить настройку"
    return f"""
    <html><head><meta charset='utf-8'><title>Production Center</title>
    <style>
      body {{ font-family: Arial, sans-serif; background:#f6f7f9; margin:32px; color:#111; }}
      h1 {{ margin-bottom:4px; }}
      .badge {{ display:inline-block; padding:8px 12px; border-radius:10px; font-weight:700; }}
      .ready {{ background:#daf5df; color:#0b6b22; }} .warn {{ background:#fff1c2; color:#7a5500; }}
      table {{ border-collapse:collapse; background:#fff; width:100%; margin-top:20px; }}
      td,th {{ border:1px solid #ddd; padding:10px; }}
      .grid {{ display:grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:20px 0; }}
      .card {{ background:#fff; padding:14px; border-radius:12px; text-decoration:none; color:#111; border:1px solid #ddd; }}
      code {{ background:#111; color:#fff; padding:5px 7px; border-radius:6px; line-height:2.1; }}
    </style></head><body>
      <h1>Production Center v27</h1>
      <div class='badge {badge}'>{title}: {status['score']}%</div>
      <h2>Быстрые ссылки</h2><div class='grid'>{links}</div>
      <h2>Команды запуска</h2><p>{commands}</p>
      <h2>Проверки</h2><table><tr><th>Проверка</th><th>Статус</th></tr>{rows}</table>
    </body></html>
    """
