from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/final-qa", tags=["final-qa"])

QA_SECTIONS = [
    {
        "title": "1. Клиентский путь Telegram",
        "items": [
            "Открыть /start и увидеть главное меню",
            "Запустить расчет неустойки",
            "Ввести стоимость ДДУ",
            "Ввести дату передачи",
            "Выбрать статус передачи объекта",
            "Получить предварительный расчет без юридических гарантий",
            "Выбрать М1 и пройти согласие ПД",
            "Открыть Мое дело и увидеть статус/следующий шаг",
        ],
    },
    {
        "title": "2. Маршрут М1",
        "items": [
            "После расчета перейти в М1",
            "Подтвердить согласие на обработку ПД",
            "Загрузить ДДУ/приложения/допсоглашения",
            "Передать документы на проверку",
            "Юрист принимает дело или запрашивает документы",
            "Договор открывает первый платеж",
            "Оплата открывает доверенность",
            "Доверенность открывает претензию",
            "30 дней после претензии контролируются scheduler",
            "Судебный этап и исполнение видны в Моем деле",
            "Success fee закрывает дело",
        ],
    },
    {
        "title": "3. Маршрут М2",
        "items": [
            "Войти в М2 через Связаться с юристом",
            "Сохранить описание ситуации",
            "Загрузить документы или пропустить",
            "Выбрать слот консультации",
            "Оплатить консультацию",
            "После оплаты консультация назначена",
            "Юрист фиксирует итог",
            "Юрист переводит в М1 или закрывает обращение",
        ],
    },
    {
        "title": "4. Администратор",
        "items": [
            "Войти в админку",
            "Открыть очередь обращений",
            "Открыть карточку дела",
            "Назначить юриста",
            "Проверить документы и оплаты",
            "Изменить суммы и сроки в настройках",
            "Выполнить ручное подтверждение оплаты при необходимости",
            "Проверить audit log",
        ],
    },
    {
        "title": "5. Юрист",
        "items": [
            "Открыть рабочее место юриста",
            "Посмотреть назначенные дела",
            "Проверить документы",
            "Принять дело в М1",
            "Запросить новую версию документа",
            "Перевести дело в М2",
            "Закрыть консультацию с решением",
        ],
    },
    {
        "title": "6. Эксплуатация",
        "items": [
            "Открыть Go Live Center",
            "Открыть Production Center",
            "Проверить /ready",
            "Проверить Health Center",
            "Проверить Diagnostic Center",
            "Проверить Backup Center",
            "Проверить Search Center",
            "Запустить acceptance.sh",
        ],
    },
]

@router.get("/status")
async def final_qa_status():
    total = sum(len(section["items"]) for section in QA_SECTIONS)
    return {
        "version": "1.0.0-v29",
        "status": "OPERATOR_QA_READY",
        "sections": len(QA_SECTIONS),
        "checks": total,
        "qa_sections": QA_SECTIONS,
    }

@router.get("/ui", response_class=HTMLResponse)
async def final_qa_ui():
    cards = []
    for section in QA_SECTIONS:
        checks = "".join(f"<label class='check'><input type='checkbox'> {item}</label>" for item in section["items"])
        cards.append(f"<section class='card'><h2>{section['title']}</h2>{checks}</section>")
    html = f"""
    <!doctype html>
    <html lang="ru">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>Final QA Center v29</title>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 0; background: #f4f5f7; color: #111; }}
        header {{ background: #111; color: white; padding: 24px 32px; }}
        main {{ max-width: 1100px; margin: 24px auto; padding: 0 16px 40px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }}
        .card {{ background: white; border-radius: 16px; padding: 20px; box-shadow: 0 8px 24px rgba(0,0,0,.06); }}
        h1 {{ margin: 0 0 8px; font-size: 28px; }}
        h2 {{ margin-top: 0; font-size: 18px; }}
        .muted {{ color: #666; }}
        .check {{ display: block; padding: 9px 0; border-bottom: 1px solid #eee; line-height: 1.35; }}
        .check:last-child {{ border-bottom: 0; }}
        nav a {{ display: inline-block; margin: 8px 8px 0 0; color: white; }}
        .note {{ background: #fff8dc; border: 1px solid #f0d98c; padding: 14px 16px; border-radius: 12px; margin-bottom: 16px; }}
      </style>
    </head>
    <body>
      <header>
        <h1>Final QA Center v29</h1>
        <div class="muted" style="color:#ccc">Финальная ручная проверка Telegram-бота перед передачей в эксплуатацию</div>
        <nav>
          <a href="/go-live/ui">Go Live</a>
          <a href="/production-center/ui">Production Center</a>
          <a href="/operator">Operator</a>
          <a href="/admin-ui">Admin</a>
          <a href="/ready">Ready JSON</a>
        </nav>
      </header>
      <main>
        <div class="note">Отмечайте пункты вручную после проверки в Telegram, админке и операторских центрах. Это контрольный лист, чтобы не выпускать бот “на честном слове и древнем проклятии”.</div>
        <div class="grid">{''.join(cards)}</div>
      </main>
    </body>
    </html>
    """
    return html
