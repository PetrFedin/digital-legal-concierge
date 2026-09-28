from __future__ import annotations

from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor
from app.system.settings_service import SettingsService

router = APIRouter(tags=["settings-ui"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


def _display_value(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _type_hint(kind: str) -> str:
    return {
        "money": "Сумма в рублях, больше 0",
        "percent": "Процент: больше 0 и не выше 100",
        "integer": "Целое число в допустимом диапазоне",
        "list": "Интервалы через запятую, например 3, 24, 72",
        "text": "Текст до 4000 символов",
    }.get(kind, "Текстовое значение")


def _redirect_notice(text: str) -> RedirectResponse:
    return RedirectResponse(
        url="/settings-ui?" + urlencode({"notice": text}),
        status_code=303,
    )


def _auth_recovery(error: DocumentAccessError | HTTPException) -> RedirectResponse | None:
    """Keep staff configuration pages fail-closed without raw JSON dead ends."""

    if error.status_code == 401:
        return RedirectResponse(url="/login?next=/settings-ui", status_code=303)
    if error.status_code in {403, 409}:
        return RedirectResponse(url="/operator", status_code=303)
    return None


@router.get("/settings-ui", response_class=HTMLResponse)
async def settings_ui(
    request: Request,
    notice: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _admin(request, db, x_admin_token)
    except (DocumentAccessError, HTTPException) as error:
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise

    items = await SettingsService(db).list_settings()
    rows: list[str] = []
    for item in items:
        stored = item.value or {}
        value = stored.get("value")
        kind = str(stored.get("type") or "string")
        editable = bool(item.is_editable_in_admin)
        rows.append(
            f"""
            <article class="setting {'locked' if not editable else ''}">
              <div class="setting-head">
                <div><h3>{escape(item.title or item.key)}</h3><code>{escape(item.key)}</code></div>
                <span class="pill">{escape(kind)}</span>
              </div>
              <p class="hint">{escape(_type_hint(kind))}</p>
              <form method="post" action="/settings-ui/update">
                <input type="hidden" name="key" value="{escape(item.key, quote=True)}">
                <input type="hidden" name="expected_updated_at" value="{escape(item.updated_at.isoformat(), quote=True)}">
                <input name="value" value="{escape(_display_value(value), quote=True)}" {'disabled' if not editable else ''} autocomplete="off">
                <button type="submit" {'disabled' if not editable else ''}>Сохранить изменение</button>
              </form>
              <div class="meta">Последнее изменение: {escape(item.updated_at.strftime('%d.%m.%Y %H:%M UTC'))}</div>
            </article>
            """
        )

    notice_html = (
        f'<div class="notice">{escape(notice)}</div>'
        if notice
        else '<div class="notice subtle">Изменения применяются сразу. Суммы, проценты и сроки валидируются сервером; конфликт параллельного редактирования не перезаписывается молча.</div>'
    )
    html = f"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Настройки сервиса</title>
<style>
:root{{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--blue2:#eef2ff;--green:#14804a;--amber:#a15c00;--amber2:#fff7e6;--shadow:0 10px 28px rgba(16,24,40,.07)}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}}header{{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 22px}}.head,main{{max-width:1120px;margin:auto}}.head{{display:flex;justify-content:space-between;gap:14px;align-items:center}}h1{{font-size:22px;margin:0 0 4px}}header p{{margin:0;color:#d0d5dd;font-size:13px}}.links{{display:flex;gap:8px;flex-wrap:wrap}}.button,button{{border:0;border-radius:10px;padding:9px 12px;background:var(--blue);color:#fff;text-decoration:none;font-weight:750;cursor:pointer}}.button.secondary{{background:#475467}}main{{padding:20px}}.notice{{border:1px solid #fedf89;background:var(--amber2);border-radius:13px;padding:12px;margin-bottom:14px;line-height:1.45}}.notice.subtle{{border-color:#c7d2fe;background:var(--blue2)}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.setting{{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px;box-shadow:var(--shadow)}}.setting.locked{{opacity:.7}}.setting-head{{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}}h3{{margin:0 0 5px;font-size:17px}}code{{font-size:11px;color:var(--muted);word-break:break-all}}.pill{{background:#eef2f6;border-radius:999px;padding:5px 8px;font-size:11px;font-weight:800}}.hint,.meta{{color:var(--muted);font-size:12px;line-height:1.4}}form{{display:flex;gap:8px;margin:10px 0}}input{{min-width:0;flex:1;padding:10px;border:1px solid #d0d5dd;border-radius:10px;font:inherit}}input:focus{{outline:0;border-color:#9db0f5;box-shadow:0 0 0 3px #eef2ff}}button:disabled,input:disabled{{opacity:.55;cursor:not-allowed}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}.head{{align-items:flex-start;flex-direction:column}}}}@media(max-width:480px){{main{{padding:12px}}form,.links{{flex-direction:column}}.button,button{{width:100%;text-align:center}}}}
</style></head><body>
<header><div class="head"><div><h1>⚙ Настройки сервиса</h1><p>Суммы, сроки, SLA и клиентские тексты — с проверкой и аудитом.</p></div><div class="links"><a class="button secondary" href="/operator">Все разделы</a><a class="button secondary" href="/admin/workdesk/ui">Рабочий стол</a><a class="button secondary" href="/diagnostic-center/ui">Диагностика</a></div></div></header>
<main>{notice_html}<section class="grid">{''.join(rows)}</section></main></body></html>
"""
    return HTMLResponse(html)


@router.post("/settings-ui/update")
async def settings_update(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    expected_updated_at: str = Form(...),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
    except (DocumentAccessError, HTTPException) as error:
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    try:
        await SettingsService(db).set_value(
            key=key,
            value=value,
            actor_id=int(actor.account_id),
            expected_updated_at=expected_updated_at,
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        return _redirect_notice(f"Изменение не сохранено: {error}")
    except Exception:
        await db.rollback()
        raise
    return _redirect_notice("Настройка сохранена. Новое значение уже используется сервисом.")
