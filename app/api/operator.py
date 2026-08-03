from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings

router = APIRouter(tags=["operator"])


def _state(enabled: bool) -> tuple[str, str]:
    return ("Работает", "ok") if enabled else ("Отключено", "warn")


def _payment_state() -> tuple[str, str, str]:
    provider = settings.payment_provider.strip().lower()
    if provider == "disabled":
        return (
            "Оплата отключена",
            "warn",
            "Бот и кабинеты доступны, платежные ссылки не создаются.",
        )
    if provider == "yookassa":
        return ("ЮKassa подключена", "ok", "Онлайн-платежи принимаются через ЮKassa.")
    return (
        f"Провайдер: {provider or 'не задан'}",
        "warn",
        "Проверьте платежные настройки перед приемом реальных оплат.",
    )


@router.get("/operator", response_class=HTMLResponse)
async def operator_page():
    bot_label, bot_class = _state(settings.run_bot)
    scheduler_label, scheduler_class = _state(settings.run_scheduler)
    payment_label, payment_class, payment_note = _payment_state()
    environment = escape(settings.app_env or "unknown")

    html = f"""
    <!doctype html>
    <html lang="ru">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Digital Legal Concierge — рабочее пространство</title>
      <style>
        :root {{
          --bg:#f3f5f9; --surface:#ffffff; --ink:#162033; --muted:#667085;
          --line:#e4e7ec; --primary:#3157d5; --primary-soft:#eef2ff;
          --ok:#14804a; --ok-bg:#ecfdf3; --warn:#a15c00; --warn-bg:#fff7e6;
          --shadow:0 12px 34px rgba(16,24,40,.07);
        }}
        * {{ box-sizing:border-box; }}
        body {{ margin:0; background:var(--bg); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; }}
        header {{ background:linear-gradient(135deg,#111827,#25304a); color:#fff; padding:28px 24px; }}
        .header-inner {{ max-width:1180px; margin:auto; display:flex; justify-content:space-between; gap:20px; align-items:center; }}
        h1 {{ margin:0 0 8px; font-size:clamp(24px,4vw,36px); }}
        header p {{ margin:0; color:#d0d5dd; }}
        .env {{ padding:7px 11px; border:1px solid rgba(255,255,255,.24); border-radius:999px; font-size:13px; white-space:nowrap; }}
        main {{ max-width:1180px; margin:auto; padding:24px; }}
        .section {{ margin-bottom:24px; }}
        .section-title {{ display:flex; justify-content:space-between; align-items:end; gap:12px; margin-bottom:12px; }}
        .section-title h2 {{ margin:0; font-size:20px; }}
        .section-title p {{ margin:0; color:var(--muted); font-size:14px; }}
        .status-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }}
        .status-card,.group {{ background:var(--surface); border:1px solid var(--line); border-radius:18px; box-shadow:var(--shadow); }}
        .status-card {{ padding:18px; }}
        .status-top {{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:10px; }}
        .status-card h3 {{ margin:0; font-size:16px; }}
        .status-card p {{ margin:0; color:var(--muted); line-height:1.45; font-size:14px; }}
        .pill {{ display:inline-flex; align-items:center; gap:6px; padding:6px 9px; border-radius:999px; font-size:12px; font-weight:750; }}
        .pill.ok {{ color:var(--ok); background:var(--ok-bg); }}
        .pill.warn {{ color:var(--warn); background:var(--warn-bg); }}
        .group-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
        .group {{ padding:18px; }}
        .group h3 {{ margin:0 0 6px; font-size:17px; }}
        .group > p {{ margin:0 0 14px; color:var(--muted); font-size:14px; }}
        .links {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px; }}
        .link {{ display:block; border:1px solid var(--line); border-radius:12px; padding:12px; color:var(--ink); text-decoration:none; background:#fff; transition:.16s ease; }}
        .link:hover {{ border-color:#b9c4f7; background:var(--primary-soft); transform:translateY(-1px); }}
        .link b {{ display:block; margin-bottom:3px; font-size:14px; }}
        .link span {{ color:var(--muted); font-size:12px; line-height:1.35; }}
        .primary {{ border-color:transparent; background:var(--primary); color:#fff; }}
        .primary:hover {{ background:#2748bd; color:#fff; }}
        .primary span {{ color:#dbe4ff; }}
        .workflow {{ background:var(--surface); border:1px solid var(--line); border-radius:18px; padding:18px; }}
        .workflow ol {{ margin:10px 0 0; padding-left:22px; color:var(--muted); line-height:1.65; }}
        footer {{ max-width:1180px; margin:0 auto; padding:0 24px 28px; color:var(--muted); font-size:13px; }}
        @media(max-width:860px) {{ .status-grid,.group-grid {{ grid-template-columns:1fr; }} }}
        @media(max-width:560px) {{ header,main {{ padding-left:16px; padding-right:16px; }} .header-inner {{ align-items:flex-start; flex-direction:column; }} .links {{ grid-template-columns:1fr; }} }}
      </style>
    </head>
    <body>
      <header>
        <div class="header-inner">
          <div>
            <h1>⚖ Digital Legal Concierge</h1>
            <p>Единая точка входа для администратора, оператора и контроля сервиса.</p>
          </div>
          <div class="env">Среда: {environment}</div>
        </div>
      </header>
      <main>
        <section class="section">
          <div class="section-title"><h2>Состояние сервиса</h2><p>Ключевые компоненты текущего запуска</p></div>
          <div class="status-grid">
            <article class="status-card">
              <div class="status-top"><h3>Telegram-бот</h3><span class="pill {bot_class}">{bot_label}</span></div>
              <p>Принимает обращения клиентов и ведет их по сценариям расчета, дела и консультации.</p>
            </article>
            <article class="status-card">
              <div class="status-top"><h3>Автоматические проверки</h3><span class="pill {scheduler_class}">{scheduler_label}</span></div>
              <p>Контролирует сроки, уведомления, консультации и системные задания.</p>
            </article>
            <article class="status-card">
              <div class="status-top"><h3>Платежи</h3><span class="pill {payment_class}">{escape(payment_label)}</span></div>
              <p>{escape(payment_note)}</p>
            </article>
          </div>
        </section>

        <section class="section">
          <div class="section-title"><h2>Рабочее пространство</h2><p>Основные действия сгруппированы по задачам</p></div>
          <div class="group-grid">
            <article class="group">
              <h3>Ежедневная работа</h3>
              <p>Очередь, дела, переписка, юристы и задачи.</p>
              <div class="links">
                <a class="link primary" href="/admin-ui"><b>Административная панель</b><span>Дела, очередь, платежи, документы и юристы</span></a>
                <a class="link" href="/message-center/ui"><b>Сообщения</b><span>Диалоги клиентов и ответы команды</span></a>
                <a class="link" href="/lawyer/ui"><b>Кабинет юриста</b><span>Назначенные дела и консультации</span></a>
                <a class="link" href="/task-center/ui"><b>Задачи</b><span>Контроль текущих операционных действий</span></a>
              </div>
            </article>
            <article class="group">
              <h3>Контроль и безопасность</h3>
              <p>Работоспособность, сроки, события и резервные копии.</p>
              <div class="links">
                <a class="link" href="/monitoring-center/ui"><b>Мониторинг</b><span>Текущее состояние приложения</span></a>
                <a class="link" href="/admin/sla/ui"><b>SLA и просрочки</b><span>Сроки реакции и эскалации</span></a>
                <a class="link" href="/security-events/ui"><b>События безопасности</b><span>Проверка подозрительных действий</span></a>
                <a class="link" href="/backup-center/ui"><b>Резервные копии</b><span>Статус и проверка backup</span></a>
              </div>
            </article>
            <article class="group">
              <h3>Настройка системы</h3>
              <p>Права, интеграции, шаблоны и параметры сервиса.</p>
              <div class="links">
                <a class="link" href="/settings-ui"><b>Настройки</b><span>Рабочие параметры приложения</span></a>
                <a class="link" href="/access/ui"><b>Пользователи и права</b><span>Роли, доступ и MFA</span></a>
                <a class="link" href="/integration-center/ui"><b>Интеграции</b><span>Подключения внешних сервисов</span></a>
                <a class="link" href="/template-builder/ui"><b>Шаблоны</b><span>Контент и документы для сценариев</span></a>
              </div>
            </article>
            <article class="group">
              <h3>Техническое обслуживание</h3>
              <p>Проверки релиза и диагностика — без смешивания с ежедневной работой.</p>
              <div class="links">
                <a class="link" href="/health-center/ui"><b>Диагностика</b><span>Health, readiness и зависимости</span></a>
                <a class="link" href="/release-manager/ui"><b>Релизы</b><span>Версия, деплой и откат</span></a>
                <a class="link" href="/audit-center/ui"><b>Аудит</b><span>Журнал и целостность действий</span></a>
                <a class="link" href="/scenario-map-ui"><b>Карта сценариев</b><span>Связи клиентских и внутренних процессов</span></a>
              </div>
            </article>
          </div>
        </section>

        <section class="section workflow">
          <h2>Как проходит обращение</h2>
          <ol>
            <li>Клиент запускает расчет или открывает существующее дело в Telegram.</li>
            <li>Обращение попадает в очередь администратора с понятным следующим действием.</li>
            <li>Администратор назначает юриста и контролирует документы и сроки.</li>
            <li>Юрист работает с делом или консультацией и фиксирует результат.</li>
            <li>Клиент получает уведомления о каждом значимом изменении.</li>
          </ol>
        </section>
      </main>
      <footer>Системные endpoints: <a href="/health">health</a> · <a href="/ready">ready</a> · <a href="/launch-check">launch-check</a></footer>
    </body>
    </html>
    """
    return HTMLResponse(html)


@router.get("/operator/status")
async def operator_status():
    return {
        "version": "1.0.0-v26",
        "bot_enabled": settings.run_bot,
        "scheduler_enabled": settings.run_scheduler,
        "payment_provider": settings.payment_provider,
        "storage_dir": settings.storage_dir,
        "public_base_url": settings.public_base_url,
        "recommended_next_step": "Откройте /operator, затем /admin-ui для ежедневной работы",
        "workspaces": {
            "admin": "/admin-ui",
            "lawyer": "/lawyer/ui",
            "messages": "/message-center/ui",
            "monitoring": "/monitoring-center/ui",
        },
    }
