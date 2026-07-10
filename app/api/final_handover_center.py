from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/final-handover", tags=["final-handover"])

HANDOVER_BLOCKS = [
    {
        "title": "1. Запуск",
        "status": "готово",
        "items": [
            "Один основной запуск через ./run.sh",
            "Меню управления через ./bot-control.sh",
            "Проверка готовности через /ready и /launch-check",
            "Production-шаблоны: nginx, systemd, docker-compose.production.yml",
        ],
    },
    {
        "title": "2. Telegram-бот",
        "status": "готово",
        "items": [
            "Команды /start, /menu, /status, /help, /cancel",
            "Главное меню клиента",
            "Экраны B-001—B-028 по UX/UI-спецификации",
            "Маршруты М1 и М2 без третьих веток",
        ],
    },
    {
        "title": "3. Клиентский путь",
        "status": "готово",
        "items": [
            "Калькулятор неустойки",
            "Согласие на обработку персональных данных",
            "Документы, оплаты, консультации, сообщения",
            "Раздел Мое дело как Telegram-кабинет",
        ],
    },
    {
        "title": "4. Операционная работа",
        "status": "готово",
        "items": [
            "Админка /admin-ui",
            "Операторский центр /operator",
            "Карточка дела, документы, оплаты, настройки",
            "Рабочее место юриста и решения по маршрутам",
        ],
    },
    {
        "title": "5. Эксплуатация",
        "status": "готово",
        "items": [
            "Health, Diagnostic, Recovery, Backup, Search, Audit, Notification Centers",
            "Final QA Center",
            "Production Center",
            "Go Live Center",
        ],
    },
    {
        "title": "6. Что нужно заполнить перед реальным запуском",
        "status": "требует данных владельца",
        "items": [
            "BOT_TOKEN от BotFather",
            "PUBLIC_BASE_URL домена с HTTPS",
            "ADMIN_USERNAME и ADMIN_PASSWORD",
            "Реквизиты компании и юридические тексты",
            "Параметры YooKassa / CloudPayments при переходе с fake-платежей",
        ],
    },
]

@router.get("/status")
async def final_handover_status():
    return {
        "version": "1.0.0-v29",
        "status": "HANDOVER_READY",
        "blocks": HANDOVER_BLOCKS,
        "main_start": "./run.sh",
        "operator_menu": "./bot-control.sh",
        "recommended_first_url": "/final-handover/ui",
        "production_urls": [
            "/ready",
            "/launch-check",
            "/production-center/ui",
            "/go-live/ui",
            "/final-qa/ui",
            "/operator",
            "/admin-ui",
        ],
    }

@router.get("/ui", response_class=HTMLResponse)
async def final_handover_ui():
    cards = []
    for block in HANDOVER_BLOCKS:
        items = "".join(f"<li>{item}</li>" for item in block["items"])
        badge_class = "ok" if block["status"] == "готово" else "warn"
        cards.append(
            f"<section class='card'><div class='row'><h2>{block['title']}</h2>"
            f"<span class='badge {badge_class}'>{block['status']}</span></div><ul>{items}</ul></section>"
        )
    html = f"""
    <!doctype html>
    <html lang="ru">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>Final Handover v29</title>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin:0; background:#f5f6f8; color:#111; }}
        header {{ background:#101010; color:#fff; padding:28px 36px; }}
        main {{ max-width:1120px; margin:24px auto; padding:0 16px 48px; }}
        h1 {{ margin:0 0 8px; font-size:30px; }}
        .muted {{ color:#666; }} header .muted {{ color:#cfcfcf; }}
        .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:16px; }}
        .card {{ background:#fff; border-radius:16px; padding:20px; box-shadow:0 8px 24px rgba(0,0,0,.06); }}
        .row {{ display:flex; align-items:center; justify-content:space-between; gap:12px; }}
        h2 {{ margin:0; font-size:18px; }}
        li {{ margin:9px 0; line-height:1.35; }}
        .badge {{ padding:6px 10px; border-radius:999px; font-size:12px; white-space:nowrap; }}
        .ok {{ background:#e8f8ee; color:#0d6b2f; }} .warn {{ background:#fff3cd; color:#7a5a00; }}
        .hero {{ background:#fff; border-radius:16px; padding:20px; margin-bottom:16px; box-shadow:0 8px 24px rgba(0,0,0,.06); }}
        .links a {{ display:inline-block; margin:8px 8px 0 0; padding:10px 12px; border-radius:10px; background:#111; color:#fff; text-decoration:none; }}
        code {{ background:#eee; padding:2px 6px; border-radius:6px; }}
      </style>
    </head>
    <body>
      <header>
        <h1>Final Handover v29</h1>
        <div class="muted">Единая страница передачи Telegram-бота в эксплуатацию</div>
      </header>
      <main>
        <section class="hero">
          <h2>Как запускать</h2>
          <p>Основная команда: <code>./run.sh</code>. Меню управления: <code>./bot-control.sh</code>.</p>
          <p class="muted">Перед реальным запуском заполните .env: BOT_TOKEN, PUBLIC_BASE_URL, доступы администратора и платежи.</p>
          <div class="links">
            <a href="/ready">Ready</a>
            <a href="/launch-check">Launch Check</a>
            <a href="/production-center/ui">Production Center</a>
            <a href="/go-live/ui">Go Live</a>
            <a href="/final-qa/ui">Final QA</a>
            <a href="/operator">Operator</a>
            <a href="/admin-ui">Admin</a>
          </div>
        </section>
        <div class="grid">{''.join(cards)}</div>
      </main>
    </body>
    </html>
    """
    return html
