from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.backup_center_guard import router as backup_center_guard_router
from app.api.refund_resolution_guard import router as refund_resolution_guard_router
from app.api.staff_ui_guards import router as staff_ui_guards_router
from app.api.staff_ui_shell_guard import router as staff_ui_shell_guard_router
from app.api.superadmin_ui_guards import router as superadmin_ui_guards_router
from app.api.workdesk_ui_guard import router as workdesk_ui_guard_router
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["operator-guard"])


def _hub_html(role: str) -> str:
    if role == ROLE_LAWYER:
        title = "Рабочие разделы юриста"
        note = "Навигация без второго кабинета: все данные и действия остаются в профильных рабочих экранах."
        links = [
            ("Мои дела", "/lawyer/workspace/ui", "Приоритеты, M1/M2, SLA и следующий шаг"),
            ("Консультации", "/lawyer/consultation-desk/ui", "Подготовка, встреча, результат и неявка клиента"),
            ("Моё расписание", "/consultation-slots/ui", "Свободные слоты только текущего юриста"),
            ("Документы", "/document-access/review/ui", "Проверка актуальных версий и решений"),
            ("Сообщения", "/message-center/ui", "Переписка только по делам в зоне ответственности"),
        ]
    else:
        title = "Рабочие разделы администратора"
        note = "Workdesk остаётся единственным источником операционных приоритетов; здесь только понятная навигация."
        links = [
            ("Единый Workdesk", "/admin/workdesk/ui", "Приоритеты, очереди, E2E-контроль и карточка дела"),
            ("Поиск", "/search-center/ui", "Защищённый поиск по делам, клиентам и операциям"),
            ("Расписание", "/consultation-slots/ui", "Слоты всех юристов и истёкшие резервы"),
            ("Контроль консультаций", "/admin/consultation-outcomes/ui", "Неявки, переносы и исторические незавершённые итоги"),
            ("Платёжная сверка", "/admin/payment-reviews/ui", "Полученные платежи, остановленные защитой"),
            ("Возвраты", "/admin/refunds/ui", "Фактический возврат, отказ и повторная попытка"),
            ("SLA", "/admin/sla/ui", "Просрочки и подтверждение обработки"),
            ("Сообщения", "/message-center/ui", "Рабочая переписка по делам"),
            ("Настройки", "/settings-ui", "Валидируемые суммы, сроки и клиентские тексты"),
            ("Диагностика", "/diagnostic-center/ui", "Живое состояние сервиса без секретов"),
        ]
        if role == ROLE_SUPERADMIN:
            links.extend(
                [
                    ("Доступы", "/access/ui", "Пользователи, роли и отзыв сессий"),
                    ("Безопасность", "/security-events/ui", "События безопасности и персональные сессии"),
                    ("Аудит", "/audit-center/ui", "Целостность и история административных действий"),
                    ("Резервные копии", "/backup-center/ui", "Проверка архивов и restore fence"),
                    ("Retention", "/retention/ui", "Legal hold и контролируемое удаление после срока хранения"),
                ]
            )
    cards = "".join(
        f'<a class="card" href="{href}"><b>{name}</b><span>{description}</span></a>'
        for name, href, description in links
    )
    return f"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
:root{{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--shadow:0 10px 28px rgba(16,24,40,.07)}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}}header{{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:22px}}.head,main{{max-width:1080px;margin:auto}}.head{{display:flex;justify-content:space-between;gap:14px;align-items:center}}h1{{margin:0 0 5px;font-size:24px}}header p{{margin:0;color:#d0d5dd;font-size:13px;line-height:1.45}}button{{border:0;border-radius:10px;padding:9px 12px;background:#475467;color:#fff;font-weight:750;cursor:pointer}}main{{padding:20px}}.intro{{background:#eef2ff;border:1px solid #c7d2fe;border-radius:14px;padding:13px;margin-bottom:14px;font-size:13px;line-height:1.5}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}}.card{{display:block;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px;text-decoration:none;color:var(--ink);box-shadow:var(--shadow);transition:.12s ease}}.card:hover,.card:focus{{transform:translateY(-1px);border-color:#aab8ee;outline:none}}.card b{{display:block;margin-bottom:5px}}.card span{{display:block;color:var(--muted);font-size:13px;line-height:1.4}}@media(max-width:680px){{.head{{align-items:flex-start;flex-direction:column}}.grid{{grid-template-columns:1fr}}main{{padding:12px}}button{{width:100%}}}}
</style></head><body><header><div class="head"><div><h1>{title}</h1><p>{note}</p></div><form method="post" action="/logout"><button type="submit">Выйти</button></form></div></header><main><div class="intro">Открывайте раздел по задаче. Данные не дублируются здесь и не создают отдельную «панель состояния».</div><section class="grid">{cards}</section></main></body></html>
"""


@router.get("/operator", response_class=HTMLResponse)
async def operator_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    try:
        actor = await resolve_document_actor(db, token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    if actor.role not in {ROLE_LAWYER, ROLE_ADMIN, ROLE_SUPERADMIN}:
        return RedirectResponse(url="/admin-ui", status_code=303)
    return HTMLResponse(_hub_html(actor.role))


# operator_guard is mounted by initial_setup_wizard before the legacy staff
# routers. Keep staff/superadmin HTML shell authentication, Workdesk deep links,
# backup authorization and M2 refund lifecycle guards in this early layer so
# stale bookmarks never fall through to a client-side-only or raw-error surface.
router.include_router(backup_center_guard_router)
router.include_router(superadmin_ui_guards_router)
router.include_router(workdesk_ui_guard_router)
router.include_router(staff_ui_shell_guard_router)
router.include_router(staff_ui_guards_router)
router.include_router(refund_resolution_guard_router)
