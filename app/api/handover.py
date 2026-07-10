from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings

router = APIRouter(tags=["handover"])


def _badge(ok: bool) -> str:
    return "<span class='ok'>готово</span>" if ok else "<span class='warn'>требует настройки</span>"


@router.get("/handover", response_class=HTMLResponse)
async def handover_page():
    bot_ready = bool(settings.bot_token and settings.bot_token != "CHANGE_ME") or not settings.run_bot
    admin_ready = bool(settings.admin_api_token and settings.admin_api_token != "dev-admin-token")
    payment_ready = settings.payment_provider == "fake" or bool(settings.yookassa_shop_id and settings.yookassa_secret_key)
    html = f"""
<!doctype html>
<html lang='ru'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Передача Telegram-бота v19</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f7f7fb;color:#111827}}
header{{background:#111827;color:#fff;padding:24px}}
main{{max-width:1180px;margin:auto;padding:22px;display:grid;gap:16px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}
.ok{{color:#16a34a;font-weight:800}}.warn{{color:#ca8a04;font-weight:800}}
code,pre{{background:#0b1020;color:#d1e7ff;border-radius:12px;padding:12px;display:block;overflow:auto}}
a.button{{display:inline-block;background:#2563eb;color:white;text-decoration:none;font-weight:700;border-radius:10px;padding:10px 14px;margin:4px}}
li{{margin:6px 0}}
@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header><h1>⚖ Передача Telegram-бота v19</h1><p>Страница для владельца, администратора и оператора: что запущено, куда нажимать и как проверить работу.</p></header>
<main>
<section class='grid'>
<div class='card'><b>Telegram</b><p>{_badge(bot_ready)}</p><p>RUN_BOT: {settings.run_bot}</p></div>
<div class='card'><b>Админ-доступ</b><p>{_badge(admin_ready)}</p><p>Токен нужен для API и панели.</p></div>
<div class='card'><b>Платежи</b><p>{_badge(payment_ready)}</p><p>Провайдер: {settings.payment_provider}</p></div>
</section>
<section class='card'>
<h2>Быстрый запуск</h2>
<pre>./run.sh</pre>
<p>Меню управления без запоминания команд:</p>
<pre>./bot-control.sh</pre>
</section>
<section class='card'>
<h2>Главные страницы</h2>
<a class='button' href='/operator'>Оператор</a>
<a class='button' href='/admin-ui'>Админка</a>
<a class='button' href='/ready'>/ready</a>
<a class='button' href='/launch-check'>/launch-check</a>
<a class='button' href='/scenario-map-ui'>Карта экранов</a>
<a class='button' href='/ops-guide'>Инструкция эксплуатации</a>
</section>
<section class='grid'>
<div class='card'><h3>Клиент</h3><ol><li>/start в Telegram</li><li>Рассчитать неустойку</li><li>Выбрать М1 или М2</li><li>Загрузить документы / оплатить / смотреть Мое дело</li></ol></div>
<div class='card'><h3>Администратор</h3><ol><li>Открыть /admin-ui</li><li>Вставить x-admin-token</li><li>Контролировать очередь, оплаты, документы</li><li>Назначать юриста и менять статусы вручную при необходимости</li></ol></div>
<div class='card'><h3>Юрист</h3><ol><li>Получить назначенное дело</li><li>Проверить документы</li><li>Принять М1 / запросить документы / перевести в М2</li><li>Зафиксировать итог консультации</li></ol></div>
</section>
<section class='card'>
<h2>Приемка перед боевым использованием</h2>
<ol>
<li>BOT_TOKEN задан, бот отвечает на /start.</li>
<li>Админка открывается, токен не равен dev-admin-token.</li>
<li>Создан хотя бы один юрист.</li>
<li>Расчет проходит до результата.</li>
<li>Документ из Telegram скачивается в storage/cases.</li>
<li>Платеж подтверждается fake/manual или через YooKassa.</li>
<li>М1 проходит до закрытия в E2E.</li>
<li>М2 проходит до консультации и перевода в М1.</li>
<li>Backup создается командой ./backup.sh.</li>
</ol>
</section>
</main>
</body>
</html>
"""
    return HTMLResponse(html)


@router.get("/handover/status")
async def handover_status():
    return {
        "version": "1.0.0-v19",
        "bot_enabled": settings.run_bot,
        "scheduler_enabled": settings.run_scheduler,
        "payment_provider": settings.payment_provider,
        "admin_token_is_default": settings.admin_api_token == "dev-admin-token",
        "bot_token_is_default": settings.bot_token == "CHANGE_ME",
        "links": ["/operator", "/admin-ui", "/ready", "/launch-check", "/scenario-map-ui", "/ops-guide", "/handover"],
    }
