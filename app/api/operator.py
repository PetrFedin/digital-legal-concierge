from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings

router = APIRouter(tags=["operator"])


def _mask(value: str | None) -> str:
    if not value or value in {"CHANGE_ME", "dev-admin-token", "dev-payment-secret"}:
        return "не задано"
    if len(value) <= 8:
        return "задано"
    return value[:4] + "..." + value[-4:]


@router.get("/operator", response_class=HTMLResponse)
async def operator_page():
    bot_status = "включен" if settings.run_bot else "выключен"
    scheduler_status = "включен" if settings.run_scheduler else "выключен"
    payment_status = settings.payment_provider
    html = f"""
    <!doctype html>
    <html lang='ru'>
    <head>
      <meta charset='utf-8'>
      <meta name='viewport' content='width=device-width, initial-scale=1'>
      <title>Operator Guide v25</title>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; margin: 0; background: #f6f7fb; color: #111827; }}
        header {{ background: #111827; color: white; padding: 22px; }}
        main {{ padding: 22px; display: grid; gap: 16px; max-width: 1100px; margin: auto; }}
        .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
        .card {{ background: white; border: 1px solid #e5e7eb; border-radius: 16px; padding: 16px; box-shadow: 0 1px 2px rgba(0,0,0,.04); }}
        a.button {{ display: inline-block; padding: 10px 14px; border-radius: 10px; background: #2563eb; color: white; text-decoration: none; font-weight: 700; margin: 4px 4px 4px 0; }}
        code, pre {{ background: #0b1020; color: #d1e7ff; border-radius: 10px; padding: 10px; display: block; overflow:auto; }}
        .ok {{ color: #16a34a; font-weight: 700; }} .warn {{ color: #ca8a04; font-weight: 700; }}
        @media(max-width: 800px) {{ .grid {{ grid-template-columns: 1fr; }} }}
      </style>
    </head>
    <body>
      <header><h1>⚖ Digital Legal Concierge — операторская страница v25</h1><p>Единая страница запуска, проверки и работы с Telegram-ботом.</p></header>
      <main>
        <section class='grid'>
          <div class='card'><b>Telegram-бот</b><p class='{'ok' if settings.run_bot else 'warn'}'>{bot_status}</p><p>BOT_TOKEN: {_mask(settings.bot_token)}</p></div>
          <div class='card'><b>Scheduler</b><p class='{'ok' if settings.run_scheduler else 'warn'}'>{scheduler_status}</p><p>Напоминания, оплаты, слоты, сроки.</p></div>
          <div class='card'><b>Платежи</b><p>{payment_status}</p><p>Для боевого запуска подключите провайдера или используйте ручное подтверждение.</p></div>
        </section>
        <section class='card'>
          <h2>Быстрые ссылки</h2>
          <a class='button' href='/admin-ui'>Открыть админку</a>
          <a class='button' href='/ready'>Проверка /ready</a>
          <a class='button' href='/security-check'>Security check</a>
          <a class='button' href='/login'>Вход в админку</a>
          <a class='button' href='/launch-check'>Launch check</a>
          <a class='button' href='/health'>Health</a>
          <a class='button' href='/scenario-map-ui'>Карта сценариев</a>
          <a class='button' href='/ops-guide'>Ops Guide</a>
          <a class='button' href='/handover'>Передача проекта</a>
          <a class='button' href='/task-center/ui'>Task Center</a>
          <a class='button' href='/settings-ui'>Настройки</a>
          <a class='button' href='/message-center/ui'>Message Center</a>
          <a class='button' href='/audit-center/ui'>Audit Center</a>
          <a class='button' href='/notification-center/ui'>Notification Center</a>
          <a class='button' href='/backup-center/ui'>Backup Center</a>
          <a class='button' href='/search-center/ui'>Search Center</a>
          <a class='button' href='/initial-setup-wizard/ui'>Initial Setup</a>
          <a class='button' href='/template-builder/ui'>Template Builder</a>
          <a class='button' href='/calculator-builder/ui'>Calculator Builder</a>
          <a class='button' href='/integration-center/ui'>Integration Center</a>
          <a class='button' href='/operations-center/ui'>Operations Center</a>
          <a class='button' href='/monitoring-center/ui'>Monitoring Center</a>
          <a class='button' href='/backup-manager/ui'>Backup Manager</a>
          <a class='button' href='/release-manager/ui'>Release Manager</a>
          <a class='button' href='/acceptance-center/ui'>Acceptance Center</a>
        </section>
        <section class='card'>
          <h2>Как запустить</h2>
          <pre>./run.sh</pre>
          <p>Если нужно меню управления:</p>
          <pre>./bot-control.sh</pre>
        </section>
        <section class='card'>
          <h2>Рабочий процесс</h2>
          <ol>
            <li>Клиент открывает Telegram-бота и запускает расчет.</li>
            <li>После результата выбирает М1 или М2.</li>
            <li>Администратор видит дело в админке и назначает юриста.</li>
            <li>Юрист проверяет документы, принимает дело или переводит маршрут.</li>
            <li>Оплаты подтверждаются автоматически через webhook или вручную в админке.</li>
          </ol>
        </section>
      </main>
    </body>
    </html>
    """
    return HTMLResponse(html)


@router.get("/operator/status")
async def operator_status():
    return {
        "version": "1.0.0-v25",
        "bot_enabled": settings.run_bot,
        "scheduler_enabled": settings.run_scheduler,
        "payment_provider": settings.payment_provider,
        "storage_dir": settings.storage_dir,
        "public_base_url": settings.public_base_url,
        "bot_token": _mask(settings.bot_token),
        "admin_token": _mask(settings.admin_api_token),
        "recommended_next_step": "Откройте /operator, /search-center/ui, /task-center/ui, /message-center/ui, /audit-center/ui и /admin-ui",
    }
