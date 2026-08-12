from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.notification_delivery import router as notification_delivery_router

router = APIRouter(tags=["notification-center"])


def _rules():
    return [
        ("Новый документ", "DOCUMENT_UPLOADED", "Юрист / администратор", "Проверить файл"),
        ("Документ проверен", "DOCUMENT_STATUS_CHANGED", "Клиент", "Посмотреть комментарий"),
        ("Оплата подтверждена", "PAYMENT_PAID", "Клиент / администратор", "Открыть следующий этап"),
        ("Консультация назначена", "M2_CONSULTATION_BOOKED", "Клиент / юрист", "Подготовиться к консультации"),
        ("Претензия направлена", "M1_CLAIM_SENT", "Клиент", "Ожидать контрольный срок"),
        ("Истек срок претензии", "CLAIM_30_DAYS_EXPIRED", "Юрист / администратор", "Решить вопрос с иском"),
        ("Фактическое взыскание", "M1_MONEY_RECEIVED", "Клиент", "Оплатить success fee"),
        ("Дело закрыто", "M1_CLOSED", "Клиент", "Открыть read-only архив дела"),
        ("Сообщение клиента", "CLIENT_MESSAGE_RECEIVED", "Юрист / администратор", "Ответить клиенту"),
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
    <title>Notification Center</title>
    <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}}
    header{{background:#111827;color:#fff;padding:22px}} main{{max-width:1100px;margin:auto;padding:22px;display:grid;gap:16px}}
    .card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
    table{{width:100%;border-collapse:collapse}} th,td{{border-bottom:1px solid #e5e7eb;text-align:left;padding:10px;vertical-align:top}} code{{background:#eef2ff;padding:2px 6px;border-radius:6px}}
    a.button{{display:inline-block;padding:10px 14px;border-radius:10px;background:#2563eb;color:#fff;text-decoration:none;font-weight:700;margin:4px 4px 4px 0}}
    .warn{{background:#fffaeb;border-color:#fedf89}}
    </style></head><body>
    <header><h1>🔔 Notification Center</h1><p>Событие → получатель → понятный следующий шаг → контролируемая доставка.</p></header>
    <main>
      <section class='card'><h2>Правила уведомлений</h2><table><thead><tr><th>Событие</th><th>Код</th><th>Получатель</th><th>Следующее действие</th></tr></thead><tbody>{rows}</tbody></table></section>
      <section class='card warn'><h2>Если Telegram не доставил уведомление</h2><p>Сохранённое событие не теряется: запись остаётся в очереди. Откройте центр доставки, проверьте ошибку и повторите только доставку — юридический статус дела повторно не меняется.</p><a class='button' href='/admin/notification-delivery/ui'>Открыть ошибки доставки</a></section>
      <section class='card'><h2>Что проверять оператору</h2><ol><li>После загрузки документа юрист видит задачу.</li><li>После оплаты клиент видит следующий этап.</li><li>Перед консультацией клиент и юрист получают напоминание.</li><li>После фактического взыскания клиент получает точный success fee.</li><li>После финальной оплаты клиент видит read-only архив закрытого дела.</li></ol></section>
      <section class='card'><a class='button' href='/operator'>Операторская</a><a class='button' href='/task-center/ui'>Task Center</a><a class='button' href='/message-center/ui'>Message Center</a><a class='button' href='/audit-center/ui'>Audit Center</a></section>
    </main></body></html>
    """)


@router.get("/notification-center/status")
async def notification_center_status():
    return {
        "version": "1.1.0-v36",
        "rules_count": len(_rules()),
        "rules": [
            {
                "title": item[0],
                "event_code": item[1],
                "recipient": item[2],
                "next_action": item[3],
            }
            for item in _rules()
        ],
        "failed_delivery_center": "/admin/notification-delivery/ui",
    }


# This router has no prefix, so the delivery router keeps its canonical
# /admin/notification-delivery/* paths while being registered through main.py's
# already-existing notification-center router entry.
router.include_router(notification_delivery_router)
