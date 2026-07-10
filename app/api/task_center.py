from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.case import Case
from app.models.payment import Payment
from app.models.document import Document
from app.models.consultation import Consultation
from app.models.message import Message

router = APIRouter(tags=["task-center"])


async def _count(db: AsyncSession, statement) -> int:
    result = await db.execute(statement)
    return int(result.scalar_one() or 0)


@router.get("/task-center/status")
async def task_center_status(db: AsyncSession = Depends(get_db)):
    data = {
        "new_cases": await _count(db, select(func.count(Case.id)).where(Case.status == "NEW")),
        "cases_without_lawyer": await _count(db, select(func.count(Case.id)).where(Case.assigned_lawyer_id.is_(None)).where(Case.status.notin_(["M1_CLOSED", "M2_CLOSED", "ARCHIVED"]))),
        "documents_on_review": await _count(db, select(func.count(Document.id)).where(Document.status == "ON_REVIEW")),
        "payments_waiting": await _count(db, select(func.count(Payment.id)).where(Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"]))),
        "consultations_booked": await _count(db, select(func.count(Consultation.id)).where(Consultation.status == "BOOKED")),
        "unread_messages": await _count(db, select(func.count(Message.id)).where(Message.is_read.is_(False))),
    }
    data["ok"] = True
    data["main_risk"] = "Проверьте оплаты, документы на проверке и дела без юриста" if any(data[k] for k in ["cases_without_lawyer", "documents_on_review", "payments_waiting", "unread_messages"]) else "Критичных задач не найдено"
    return data


@router.get("/task-center/ui", response_class=HTMLResponse)
async def task_center_ui(db: AsyncSession = Depends(get_db)):
    status = await task_center_status(db)
    rows = [
        ("Новые дела", status["new_cases"], "Открыть очередь и назначить юриста"),
        ("Дела без юриста", status["cases_without_lawyer"], "Назначить ответственного"),
        ("Документы на проверке", status["documents_on_review"], "Передать юристу / принять / запросить новую версию"),
        ("Ожидающие оплаты", status["payments_waiting"], "Проверить оплату или отправить напоминание"),
        ("Назначенные консультации", status["consultations_booked"], "Проверить календарь юристов"),
        ("Непрочитанные сообщения", status["unread_messages"], "Ответить клиентам"),
    ]
    cards = "".join(
        f"<div class='card'><h3>{name}</h3><div class='num'>{value}</div><p>{action}</p></div>"
        for name, value, action in rows
    )
    html = f"""
    <!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Task Center v21</title>
    <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}}
    header{{background:#111827;color:white;padding:24px}} main{{max-width:1100px;margin:auto;padding:22px;display:grid;gap:16px}}
    .grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}} .card{{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
    .num{{font-size:36px;font-weight:800;margin:8px 0}} a.button{{display:inline-block;background:#2563eb;color:white;text-decoration:none;padding:10px 14px;border-radius:10px;font-weight:700;margin:4px}}
    .warn{{background:#fff7ed;border-color:#fed7aa}} @media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body>
    <header><h1>✅ Task Center v21</h1><p>Операционный список: что требует внимания сегодня.</p></header>
    <main>
      <section class='card warn'><b>Главный риск:</b> {status['main_risk']}</section>
      <section class='grid'>{cards}</section>
      <section class='card'><h2>Быстрые действия</h2>
        <a class='button' href='/admin-ui'>Админка</a><a class='button' href='/operator'>Оператор</a><a class='button' href='/health-center/ui'>Health Center</a><a class='button' href='/settings-ui'>Настройки</a><a class='button' href='/task-center/status'>JSON status</a>
      </section>
      <section class='card'><h2>Как работать</h2><ol><li>Сначала дела без юриста.</li><li>Потом документы на проверке.</li><li>Потом ожидающие оплаты.</li><li>Потом сообщения и консультации.</li></ol></section>
    </main></body></html>
    """
    return HTMLResponse(html)
