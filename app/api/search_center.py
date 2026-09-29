from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.case import Case
from app.models.document import Document
from app.models.message import Message
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["search-center"])
MAX_QUERY_LENGTH = 120


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


def _pattern(query: str) -> str:
    clean = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{clean}%"


def _ilike(column, pattern: str):
    return column.ilike(pattern, escape="\\")


async def _search(db: AsyncSession, query: str) -> dict:
    clean = str(query or "").strip()
    if not clean:
        return {
            "query": "",
            "results": {"cases": [], "clients": [], "documents": [], "payments": [], "messages": []},
        }
    if len(clean) > MAX_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail=f"Поисковый запрос не должен быть длиннее {MAX_QUERY_LENGTH} символов")
    pattern = _pattern(clean)

    cases = list(
        (
            await db.execute(
                select(Case)
                .where(
                    or_(
                        _ilike(Case.case_number, pattern),
                        _ilike(Case.status, pattern),
                        _ilike(Case.route, pattern),
                        _ilike(Case.title, pattern),
                    )
                )
                .order_by(Case.updated_at.desc(), Case.id.desc())
                .limit(20)
            )
        ).scalars().all()
    )
    users = list(
        (
            await db.execute(
                select(User)
                .where(
                    or_(
                        _ilike(User.full_name, pattern),
                        _ilike(User.telegram_username, pattern),
                        _ilike(User.phone, pattern),
                        _ilike(User.email, pattern),
                        _ilike(cast(User.telegram_id, String), pattern),
                    )
                )
                .order_by(User.updated_at.desc(), User.id.desc())
                .limit(20)
            )
        ).scalars().all()
    )

    # Searching by a person's name/Telegram/phone used to end at a client row
    # with no route into the actual work. Pull that client's cases into the same
    # result set and remember the newest one for a one-click Workdesk handoff.
    latest_case_by_client: dict[int, int] = {}
    if users:
        client_ids = [int(item.id) for item in users]
        related_cases = list(
            (
                await db.execute(
                    select(Case)
                    .where(Case.client_id.in_(client_ids))
                    .order_by(Case.updated_at.desc(), Case.id.desc())
                    .limit(80)
                )
            ).scalars().all()
        )
        for item in related_cases:
            latest_case_by_client.setdefault(int(item.client_id), int(item.id))
        merged = {int(item.id): item for item in cases}
        for item in related_cases:
            merged.setdefault(int(item.id), item)
        cases = sorted(
            merged.values(),
            key=lambda item: (item.updated_at, item.id),
            reverse=True,
        )[:20]

    documents = list(
        (
            await db.execute(
                select(Document)
                .where(
                    or_(
                        _ilike(Document.title, pattern),
                        _ilike(Document.file_name, pattern),
                        _ilike(Document.document_type, pattern),
                        _ilike(Document.status, pattern),
                    )
                )
                .order_by(Document.updated_at.desc(), Document.id.desc())
                .limit(20)
            )
        ).scalars().all()
    )
    payments = list(
        (
            await db.execute(
                select(Payment)
                .where(
                    or_(
                        _ilike(Payment.title, pattern),
                        _ilike(Payment.payment_code, pattern),
                        _ilike(Payment.status, pattern),
                        _ilike(cast(Payment.id, String), pattern),
                    )
                )
                .order_by(Payment.updated_at.desc(), Payment.id.desc())
                .limit(20)
            )
        ).scalars().all()
    )
    messages = list(
        (
            await db.execute(
                select(Message)
                .where(_ilike(Message.text, pattern))
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(20)
            )
        ).scalars().all()
    )

    return {
        "query": clean,
        "results": {
            "cases": [
                {
                    "id": item.id,
                    "case_number": item.case_number,
                    "route": item.route,
                    "status": item.status,
                    "client_id": item.client_id,
                }
                for item in cases
            ],
            "clients": [
                {
                    "id": item.id,
                    "telegram_id": item.telegram_id,
                    "username": item.telegram_username,
                    "full_name": item.full_name,
                    "phone": item.phone,
                    "email": item.email,
                    "latest_case_id": latest_case_by_client.get(int(item.id)),
                }
                for item in users
            ],
            "documents": [
                {
                    "id": item.id,
                    "case_id": item.case_id,
                    "title": item.title,
                    "file_name": item.file_name,
                    "status": item.status,
                    "version": item.version,
                }
                for item in documents
            ],
            "payments": [
                {
                    "id": item.id,
                    "case_id": item.case_id,
                    "title": item.title,
                    "amount": str(item.amount),
                    "status": item.status,
                    "payment_code": item.payment_code,
                }
                for item in payments
            ],
            "messages": [
                {
                    "id": item.id,
                    "case_id": item.case_id,
                    "sender_type": item.sender_type,
                    "text": str(item.text or "")[:300],
                    "is_read": item.is_read,
                }
                for item in messages
            ],
        },
    }


@router.get("/search-center/status")
async def search_status(
    request: Request,
    q: str = Query(default="", description="Поиск по делу, клиенту, документу, оплате, сообщению"),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    return await _search(db, q)


def _cell(value) -> str:
    return escape("" if value is None else str(value))


def _rows(
    items: list[dict],
    fields: list[str],
    *,
    case_field: str | None = None,
    target_prefix: str | None = None,
) -> str:
    if not items:
        return "<tr><td colspan='10'>Нет совпадений</td></tr>"
    rows: list[str] = []
    for item in items:
        cells = "".join(f"<td>{_cell(item.get(field))}</td>" for field in fields)
        action = ""
        if case_field and target_prefix:
            case_id = int(item.get(case_field) or 0)
            if case_id > 0:
                href = f"{target_prefix}{case_id}"
                action = f'<td><a class="mini" href="{escape(href, quote=True)}">Открыть</a></td>'
            else:
                action = "<td><span class='muted'>Нет дела</span></td>"
        rows.append(f"<tr>{cells}{action}</tr>")
    return "".join(rows)


@router.get("/search-center/ui", response_class=HTMLResponse)
async def search_ui(
    request: Request,
    q: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _admin(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login?next=/search-center/ui", status_code=303)
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    data = await _search(db, q)
    results = data["results"]
    query = data["query"]

    html = f"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Поиск</title><style>
:root{{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--shadow:0 10px 28px rgba(16,24,40,.06)}}*{{box-sizing:border-box}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:var(--bg);color:var(--ink)}}header{{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 22px}}.head,main{{max-width:1240px;margin:auto}}.head{{display:flex;justify-content:space-between;gap:12px;align-items:center}}header h1{{margin:0 0 4px;font-size:22px}}header p{{margin:0;color:#d0d5dd;font-size:13px}}main{{padding:20px;display:grid;gap:13px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px;box-shadow:var(--shadow);overflow:auto}}form{{display:flex;gap:8px}}input{{min-width:0;flex:1;padding:11px;border:1px solid #d0d5dd;border-radius:10px;font:inherit}}button,.button,.mini{{border:0;border-radius:9px;padding:9px 12px;background:var(--blue);color:#fff;text-decoration:none;font-weight:750;cursor:pointer}}.button.secondary{{background:#475467}}.mini{{display:inline-block;padding:6px 9px;font-size:12px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;max-width:340px;overflow-wrap:anywhere}}th{{color:var(--muted);font-size:11px;text-transform:uppercase}}h2{{font-size:17px;margin:0 0 10px}}.privacy,.muted{{color:var(--muted);font-size:12px;line-height:1.45}}@media(max-width:620px){{.head,form{{align-items:stretch;flex-direction:column}}main{{padding:12px}}.button,button{{text-align:center}}}}
</style></head><body><header><div class="head"><div><h1>🔎 Поиск по рабочей базе</h1><p>Только для администратора: дела, клиенты, документы, платежи и переписка.</p></div><a class="button secondary" href="/admin/workdesk/ui">Рабочий стол</a></div></header><main>
<section class="card"><form method="get" action="/search-center/ui"><input name="q" maxlength="{MAX_QUERY_LENGTH}" value="{escape(query, quote=True)}" placeholder="Номер дела, ФИО, Telegram, файл, платёж или фрагмент сообщения"><button type="submit">Найти</button></form><p class="privacy">Результаты содержат клиентские данные. Поиск по клиенту сразу показывает связанные дела и ведёт в Workdesk. Не передавайте этот экран вне юридической команды. Поиск ограничен 20 результатами на тип.</p></section>
<section class="card"><h2>Дела</h2><table><tr><th>ID</th><th>Номер</th><th>Маршрут</th><th>Статус</th><th>Клиент</th><th></th></tr>{_rows(results['cases'], ['id','case_number','route','status','client_id'], case_field='id', target_prefix='/admin/workdesk/ui?case_id=')}</table></section>
<section class="card"><h2>Клиенты</h2><table><tr><th>ID</th><th>Telegram ID</th><th>Username</th><th>ФИО</th><th>Телефон</th><th>Email</th><th></th></tr>{_rows(results['clients'], ['id','telegram_id','username','full_name','phone','email'], case_field='latest_case_id', target_prefix='/admin/workdesk/ui?case_id=')}</table></section>
<section class="card"><h2>Документы</h2><table><tr><th>ID</th><th>Дело</th><th>Название</th><th>Файл</th><th>Статус</th><th>Версия</th><th></th></tr>{_rows(results['documents'], ['id','case_id','title','file_name','status','version'], case_field='case_id', target_prefix='/document-access/ui?case_id=')}</table></section>
<section class="card"><h2>Оплаты</h2><table><tr><th>ID</th><th>Дело</th><th>Название</th><th>Сумма</th><th>Статус</th><th>Код</th><th></th></tr>{_rows(results['payments'], ['id','case_id','title','amount','status','payment_code'], case_field='case_id', target_prefix='/admin/workdesk/ui?case_id=')}</table></section>
<section class="card"><h2>Сообщения</h2><table><tr><th>ID</th><th>Дело</th><th>Отправитель</th><th>Текст</th><th>Прочитано</th><th></th></tr>{_rows(results['messages'], ['id','case_id','sender_type','text','is_read'], case_field='case_id', target_prefix='/message-center/ui?case_id=')}</table></section>
</main></body></html>"""
    return HTMLResponse(html)