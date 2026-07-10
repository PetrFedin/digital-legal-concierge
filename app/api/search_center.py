from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select, or_, cast, String

from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.models.user import User
from app.models.document import Document
from app.models.payment import Payment
from app.models.message import Message

router = APIRouter(tags=["search-center"])


def _like(q: str) -> str:
    return f"%{q.strip()}%"


@router.get("/search-center/status")
async def search_status(q: str = Query(default="", description="Поиск по делу, клиенту, документу, оплате, сообщению")):
    query = q.strip()
    if not query:
        return {"query": query, "results": {"cases": [], "clients": [], "documents": [], "payments": [], "messages": []}}

    pattern = _like(query)
    async with AsyncSessionLocal() as db:
        cases_result = await db.execute(
            select(Case)
            .where(or_(Case.case_number.ilike(pattern), Case.status.ilike(pattern), Case.route.ilike(pattern), Case.title.ilike(pattern)))
            .order_by(Case.updated_at.desc())
            .limit(20)
        )
        users_result = await db.execute(
            select(User)
            .where(or_(User.full_name.ilike(pattern), User.telegram_username.ilike(pattern), User.phone.ilike(pattern), User.email.ilike(pattern), cast(User.telegram_id, String).ilike(pattern)))
            .order_by(User.updated_at.desc())
            .limit(20)
        )
        docs_result = await db.execute(
            select(Document)
            .where(or_(Document.title.ilike(pattern), Document.file_name.ilike(pattern), Document.document_type.ilike(pattern), Document.status.ilike(pattern)))
            .order_by(Document.updated_at.desc())
            .limit(20)
        )
        payments_result = await db.execute(
            select(Payment)
            .where(or_(Payment.title.ilike(pattern), Payment.payment_code.ilike(pattern), Payment.status.ilike(pattern), cast(Payment.id, String).ilike(pattern)))
            .order_by(Payment.updated_at.desc())
            .limit(20)
        )
        messages_result = await db.execute(
            select(Message)
            .where(Message.text.ilike(pattern))
            .order_by(Message.created_at.desc())
            .limit(20)
        )

        cases = cases_result.scalars().all()
        users = users_result.scalars().all()
        docs = docs_result.scalars().all()
        payments = payments_result.scalars().all()
        messages = messages_result.scalars().all()

    return {
        "query": query,
        "results": {
            "cases": [{"id": c.id, "case_number": c.case_number, "route": c.route, "status": c.status, "client_id": c.client_id} for c in cases],
            "clients": [{"id": u.id, "telegram_id": u.telegram_id, "username": u.telegram_username, "full_name": u.full_name, "phone": u.phone, "email": u.email} for u in users],
            "documents": [{"id": d.id, "case_id": d.case_id, "title": d.title, "file_name": d.file_name, "status": d.status, "version": d.version} for d in docs],
            "payments": [{"id": p.id, "case_id": p.case_id, "title": p.title, "amount": str(p.amount), "status": p.status, "payment_code": p.payment_code} for p in payments],
            "messages": [{"id": m.id, "case_id": m.case_id, "sender_type": m.sender_type, "text": m.text[:300], "is_read": m.is_read} for m in messages],
        },
    }


@router.get("/search-center/ui", response_class=HTMLResponse)
async def search_ui(q: str = ""):
    data = await search_status(q=q) if q else {"query": "", "results": {"cases": [], "clients": [], "documents": [], "payments": [], "messages": []}}
    results = data["results"]

    def rows(items, fields):
        if not items:
            return "<tr><td colspan='10'>Нет данных</td></tr>"
        html = ""
        for item in items:
            html += "<tr>" + "".join(f"<td>{item.get(field) or ''}</td>" for field in fields) + "</tr>"
        return html

    html = f"""
    <!doctype html>
    <html lang='ru'>
    <head>
      <meta charset='utf-8'>
      <meta name='viewport' content='width=device-width, initial-scale=1'>
      <title>Search Center v24</title>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; margin:0; background:#f6f7fb; color:#111827; }}
        header {{ background:#111827; color:white; padding:22px; }}
        main {{ max-width:1200px; margin:auto; padding:22px; display:grid; gap:16px; }}
        .card {{ background:white; border:1px solid #e5e7eb; border-radius:16px; padding:16px; box-shadow:0 1px 2px rgba(0,0,0,.04); }}
        input {{ width:70%; padding:12px; border:1px solid #cbd5e1; border-radius:10px; font-size:16px; }}
        button, a.button {{ padding:12px 14px; border-radius:10px; background:#2563eb; color:white; border:none; text-decoration:none; font-weight:700; }}
        table {{ width:100%; border-collapse:collapse; font-size:14px; }}
        th, td {{ border-bottom:1px solid #e5e7eb; padding:8px; text-align:left; vertical-align:top; }}
        th {{ color:#6b7280; }}
        .hint {{ color:#6b7280; }}
      </style>
    </head>
    <body>
      <header><h1>🔎 Search Center v24</h1><p>Единый поиск по делам, клиентам, документам, оплатам и сообщениям.</p></header>
      <main>
        <section class='card'>
          <form method='get' action='/search-center/ui'>
            <input name='q' value='{q}' placeholder='Введите номер дела, ФИО, telegram, статус, файл, оплату или текст сообщения'>
            <button type='submit'>Найти</button>
            <a class='button' href='/operator'>Оператор</a>
          </form>
          <p class='hint'>Поиск работает внутри текущей базы бота. Для MVP без лишней магии, зато понятно.</p>
        </section>
        <section class='card'><h2>Дела</h2><table><tr><th>ID</th><th>Номер</th><th>Маршрут</th><th>Статус</th><th>Клиент</th></tr>{rows(results['cases'], ['id','case_number','route','status','client_id'])}</table></section>
        <section class='card'><h2>Клиенты</h2><table><tr><th>ID</th><th>Telegram ID</th><th>Username</th><th>ФИО</th><th>Телефон</th><th>Email</th></tr>{rows(results['clients'], ['id','telegram_id','username','full_name','phone','email'])}</table></section>
        <section class='card'><h2>Документы</h2><table><tr><th>ID</th><th>Дело</th><th>Тип</th><th>Файл</th><th>Статус</th><th>Версия</th></tr>{rows(results['documents'], ['id','case_id','title','file_name','status','version'])}</table></section>
        <section class='card'><h2>Оплаты</h2><table><tr><th>ID</th><th>Дело</th><th>Название</th><th>Сумма</th><th>Статус</th><th>Код</th></tr>{rows(results['payments'], ['id','case_id','title','amount','status','payment_code'])}</table></section>
        <section class='card'><h2>Сообщения</h2><table><tr><th>ID</th><th>Дело</th><th>Отправитель</th><th>Текст</th><th>Прочитано</th></tr>{rows(results['messages'], ['id','case_id','sender_type','text','is_read'])}</table></section>
      </main>
    </body>
    </html>
    """
    return HTMLResponse(html)
