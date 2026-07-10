from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["scenario-map"])

SCREENS = [
    {"id":"B-001","title":"🏠 Главная","route":"Общий","purpose":"точка входа и возврата"},
    {"id":"B-002","title":"🧮 Рассчитать неустойку","route":"Общий","purpose":"запуск калькулятора"},
    {"id":"B-003","title":"Ввод стоимости объекта","route":"Общий","purpose":"получить цену ДДУ"},
    {"id":"B-004","title":"Ввод даты передачи по ДДУ","route":"Общий","purpose":"получить плановую дату"},
    {"id":"B-005","title":"Статус передачи объекта","route":"Общий","purpose":"определить дату окончания расчета"},
    {"id":"B-006","title":"Результат расчета","route":"Общий","purpose":"показать предварительный расчет"},
    {"id":"B-007","title":"Согласие на обработку ПД","route":"М1","purpose":"получить согласие перед документами"},
    {"id":"B-008","title":"📄 Документы","route":"М1/М2","purpose":"загрузка и список файлов"},
    {"id":"B-009","title":"Проверка юристом","route":"М1","purpose":"ожидание решения юриста"},
    {"id":"B-010","title":"Договор","route":"М1","purpose":"подписание договора"},
    {"id":"B-011","title":"Оплата первого платежа","route":"М1","purpose":"первый платеж по договору"},
    {"id":"B-012","title":"Доверенность","route":"М1","purpose":"инструкция по доверенности"},
    {"id":"B-013","title":"Претензия","route":"М1","purpose":"статус претензии"},
    {"id":"B-014","title":"Суд","route":"М1","purpose":"судебные события"},
    {"id":"B-015","title":"Исполнение","route":"М1","purpose":"исполнительное производство"},
    {"id":"B-016","title":"📁 Мое дело","route":"М1/М2","purpose":"личный кабинет в Telegram"},
    {"id":"B-017","title":"💬 Связаться с юристом","route":"М1/М2","purpose":"сообщение или консультация"},
    {"id":"B-018","title":"Описание ситуации","route":"М2","purpose":"собрать контекст консультации"},
    {"id":"B-019","title":"Выбор времени консультации","route":"М2","purpose":"выбор слота"},
    {"id":"B-020","title":"Оплата консультации","route":"М2","purpose":"оплата консультации"},
    {"id":"B-021","title":"Консультация назначена","route":"М2","purpose":"статус консультации"},
    {"id":"B-022","title":"Сообщение юристу по делу","route":"М1/М2","purpose":"отправка вопроса"},
    {"id":"B-023","title":"Нет активного дела","route":"Общий","purpose":"пустое состояние"},
    {"id":"B-024","title":"Список документов","route":"М1/М2","purpose":"просмотр файлов"},
    {"id":"B-025","title":"Карточка документа","route":"М1/М2","purpose":"детали файла"},
    {"id":"B-026","title":"Оплаты","route":"М1/М2","purpose":"список платежей"},
    {"id":"B-027","title":"История дела","route":"М1/М2","purpose":"журнал клиентских событий"},
    {"id":"B-028","title":"Ошибка/технический сбой","route":"Общий","purpose":"обработка ошибки без потери данных"},
]

M1_FLOW = ["B-001","B-002","B-003","B-004","B-005","B-006","B-007","B-008","B-009","B-010","B-011","B-012","B-013","B-014","B-015","B-016"]
M2_FLOW = ["B-001","B-017","B-018","B-008","B-019","B-020","B-021","B-016"]

@router.get("/scenario-map")
async def scenario_map():
    return {"version":"v19","screens":SCREENS,"routes":{"M1":M1_FLOW,"M2":M2_FLOW}}

@router.get("/scenario-map-ui", response_class=HTMLResponse)
async def scenario_map_ui():
    rows = "".join(f"<tr><td>{s['id']}</td><td>{s['title']}</td><td>{s['route']}</td><td>{s['purpose']}</td></tr>" for s in SCREENS)
    m1 = " → ".join(M1_FLOW)
    m2 = " → ".join(M2_FLOW)
    html = f"""
    <!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Scenario Map v19</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}}header{{background:#111827;color:#fff;padding:22px}}main{{padding:22px;max-width:1100px;margin:auto;display:grid;gap:16px}}.card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left}}code{{background:#111827;color:#d1e7ff;padding:8px;border-radius:8px;display:block;white-space:normal;line-height:1.7}}</style></head>
    <body><header><h1>Карта сценариев Telegram-бота v19</h1><p>B-001—B-028, маршруты М1/М2 и контроль соответствия спецификациям.</p></header><main>
    <section class='card'><h2>М1 — стандартное взыскание</h2><code>{m1}</code></section>
    <section class='card'><h2>М2 — личная консультация</h2><code>{m2}</code></section>
    <section class='card'><h2>Каталог экранов</h2><table><thead><tr><th>ID</th><th>Экран</th><th>Маршрут</th><th>Назначение</th></tr></thead><tbody>{rows}</tbody></table></section>
    </main></body></html>
    """
    return HTMLResponse(html)
