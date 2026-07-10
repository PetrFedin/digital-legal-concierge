from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["notification-center"])


def _rules():
    return [
        ("Новый документ", "DOCUMENT_UPLOADED", "Юрист / администратор", "Проверить файл"),
        ("Документ проверен", "DOCUMENT_STATUS_CHANGED", "Клиент", "Посмотреть комментарий"),
        ("Оплата подтверждена", "PAYMENT_PAID", "Клиент / администратор", "Открыть следующий этап"),
        ("Консультация назначена", "M2_CONSULTATION_BOOKED", "Клиент / юрист", "Подготовиться к консультации"),
        ("Претензия направлена", "M1_CLAIM_SENT", "Клиент / юрист", "Ожидание 30 дней"),
        ("Истек срок претензии", "CLAIM_30_DAYS_EXPIRED", "Юрист / администратор", "Решить вопрос с иском"),
        ("Сообщение клиента", "MESSAGE_CREATED", "Юрист / администратор", "Ответить клиенту"),
        ("Оплата просрочена", "PAYMENT_REMINDER", "Клиент", "Вернуться к оплате"),
    ]


@router.get("/notification-center/ui", response_class=HTMLResponse)
async def notification_center_ui():
    rows = "".join(
        f"<tr><td>{title}</td><td><code>{code}</code></td><td>{to}</td><td>{action}</td></tr>"
        for title, code, to, action in _rules()
    )
    return HTMLResponse(f"""
    <!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Notification Center v23</title>
    <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}}
    header{{background:#111827;color:#fff;padding:22px}} main{{max-width:1100px;margin:auto;padding:22px;display:grid;gap:16px}}
    .card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
    table{{width:100%;border-collapse:collapse}} th,td{{border-bottom:1px solid #e5e7eb;text-align:left;padding:10px;vertical-align:top}} code{{background:#eef2ff;padding:2px 6px;border-radius:6px}}
    a.button{{display:inline-block;padding:10px 14px;border-radius:10px;background:#2563eb;color:#fff;text-decoration:none;font-weight:700;margin:4px 4px 4px 0}}
    </style></head><body>
    <header><h1>🔔 Notification Center v23</h1><p>Карта уведомлений Telegram-бота: какое событие кому отправляется и что должно произойти дальше.</p></header>
    <main>
      <section class='card'><h2>Правила уведомлений</h2><table><thead><tr><th>Событие</th><th>Код</th><th>Получатель</th><th>Следующее действие</th></tr></thead><tbody>{rows}</tbody></table></section>
      <section class='card'><h2>Что проверять оператору</h2><ol><li>После загрузки документа юрист видит задачу.</li><li>После оплаты клиент видит следующий этап.</li><li>Перед консультацией клиент и юрист получают напоминание.</li><li>После 30 дней по претензии юрист получает задачу на судебный этап.</li></ol></section>
      <section class='card'><a class='button' href='/operator'>Операторская</a><a class='button' href='/task-center/ui'>Task Center</a><a class='button' href='/message-center/ui'>Message Center</a><a class='button' href='/audit-center/ui'>Audit Center</a></section>
    </main></body></html>
    """)


@router.get("/notification-center/status")
async def notification_center_status():
    return {"version":"1.0.0-v23","rules_count":len(_rules()),"rules":[{"title":r[0],"event_code":r[1],"recipient":r[2],"next_action":r[3]} for r in _rules()]}
