from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings

router = APIRouter(tags=["launch-assistant"])


def _status() -> dict:
    storage = Path(settings.storage_dir)
    checks = {
        "BOT_TOKEN задан": bool(settings.bot_token and settings.bot_token != "CHANGE_ME"),
        "Админ-пароль задан": bool(settings.admin_password) and settings.admin_password not in {"admin", "password", "123456"},
        "ADMIN_API_TOKEN изменен": bool(settings.admin_api_token and settings.admin_api_token != "dev-admin-token"),
        "Хранилище документов создано": storage.exists(),
        "DATABASE_URL задан": bool(settings.database_url),
        "PUBLIC_BASE_URL задан": bool(settings.public_base_url),
        "Платежи настроены": settings.payment_provider == "fake" or bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
        "Webhook secret изменен": bool(settings.payment_webhook_secret and settings.payment_webhook_secret not in {"dev-payment-secret", "change-this-payment-secret"}),
    }
    return {
        "ok": all(checks.values()),
        "version": "1.0.0-v19",
        "env": settings.app_env,
        "payment_provider": settings.payment_provider,
        "run_bot": settings.run_bot,
        "run_scheduler": settings.run_scheduler,
        "checks": checks,
        "links": {
            "admin": "/admin-ui",
            "operator": "/operator",
            "handover": "/handover",
            "scenario_map": "/scenario-map-ui",
            "ops_guide": "/ops-guide",
            "ready": "/ready",
            "security": "/security-check",
        },
    }


@router.get("/launch-assistant/status")
async def launch_assistant_status():
    return _status()


@router.get("/launch-assistant", response_class=HTMLResponse)
async def launch_assistant():
    data = _status()
    rows = "".join(
        f"<tr><td>{name}</td><td>{'✅' if ok else '❌'}</td></tr>"
        for name, ok in data["checks"].items()
    )
    links = "".join(
        f"<a href='{href}'>{name}</a>" for name, href in data["links"].items()
    )
    return HTMLResponse(f"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Launch Assistant v19</title>
<style>
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f5f6fa;color:#111827}}
header{{background:#111827;color:white;padding:18px 24px}}main{{padding:24px;display:grid;gap:16px;max-width:1100px;margin:auto}}
.card{{background:white;border:1px solid #e5e7eb;border-radius:18px;padding:18px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}}
a{{display:inline-block;margin:6px 8px 6px 0;padding:9px 12px;border-radius:10px;background:#2563eb;color:white;text-decoration:none;font-weight:700}}
.ok{{color:#16a34a;font-weight:800}}.bad{{color:#dc2626;font-weight:800}}code{{background:#f3f4f6;padding:2px 6px;border-radius:6px}}
</style></head>
<body><header><h1>⚖ Launch Assistant v19</h1><div>Статус запуска Telegram-бота</div></header>
<main>
<section class="card"><h2>{'✅ Можно запускать' if data['ok'] else '⚠️ Нужно дозаполнить настройки'}</h2>
<p>Версия: <code>{data['version']}</code> · Среда: <code>{data['env']}</code> · Платежи: <code>{data['payment_provider']}</code></p>
<p>BOT: <b>{data['run_bot']}</b> · Scheduler: <b>{data['run_scheduler']}</b></p></section>
<section class="card"><h2>Проверки</h2><table><tbody>{rows}</tbody></table></section>
<section class="card"><h2>Быстрые ссылки</h2>{links}</section>
<section class="card"><h2>Как запустить</h2><pre>./run.sh
# или
./bot-control.sh</pre><p>Для production: заполнить <code>.env</code>, включить <code>RUN_BOT=true</code>, настроить <code>PUBLIC_BASE_URL</code> и платежи.</p></section>
</main></body></html>
""")
