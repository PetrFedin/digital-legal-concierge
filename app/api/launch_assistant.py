from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["launch-assistant"])
VERSION = "1.0.0-v46"


def _effective_token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_admin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await resolve_document_actor(
        db,
        _effective_token(request, header_token),
    )
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


async def _ui_admin_or_redirect(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    try:
        return await _require_admin(request, db, header_token)
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


def _status() -> dict:
    storage = Path(settings.storage_dir)
    checks = {
        "BOT_TOKEN задан": bool(settings.bot_token and settings.bot_token != "CHANGE_ME")
        or not settings.run_bot,
        "Админ-пароль задан": bool(settings.admin_password)
        and settings.admin_password not in {"admin", "password", "123456"},
        "ADMIN_API_TOKEN изменён": bool(
            settings.admin_api_token
            and settings.admin_api_token != "dev-admin-token"
        ),
        "Хранилище документов создано": storage.exists(),
        "DATABASE_URL задан": bool(settings.database_url),
        "PUBLIC_BASE_URL задан": bool(settings.public_base_url),
        "Платежи настроены": settings.payment_provider in {"fake", "disabled"}
        or bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
        "Webhook secret изменён": bool(
            settings.payment_webhook_secret
            and settings.payment_webhook_secret
            not in {"dev-payment-secret", "change-this-payment-secret"}
        ),
    }
    return {
        "ok": all(checks.values()),
        "version": VERSION,
        "env": settings.app_env,
        "payment_provider": settings.payment_provider,
        "run_bot": settings.run_bot,
        "run_scheduler": settings.run_scheduler,
        "checks": checks,
        "links": {
            "Рабочее пространство": "/operator",
            "Workdesk": "/admin/workdesk/ui",
            "Диагностика": "/diagnostic-center/ui",
            "Готовность": "/ready",
            "Безопасность": "/security-events/ui",
        },
    }


@router.get("/launch-assistant/status")
async def launch_assistant_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return _status()


@router.get("/launch-assistant", response_class=HTMLResponse)
async def launch_assistant(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    gate = await _ui_admin_or_redirect(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate

    data = _status()
    rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{'✅' if ok else '❌'}</td></tr>"
        for name, ok in data["checks"].items()
    )
    links = "".join(
        f"<a href='{escape(href, quote=True)}'>{escape(name)}</a>"
        for name, href in data["links"].items()
    )
    return HTMLResponse(
        f"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Проверка запуска — {VERSION}</title>
<style>
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f5f6fa;color:#111827}}
header{{background:#111827;color:white;padding:18px 24px}}main{{padding:24px;display:grid;gap:16px;max-width:1000px;margin:auto}}
.card{{background:white;border:1px solid #e5e7eb;border-radius:18px;padding:18px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}}
a{{display:inline-block;margin:6px 8px 6px 0;padding:9px 12px;border-radius:10px;background:#2563eb;color:white;text-decoration:none;font-weight:700}}
code{{background:#f3f4f6;padding:2px 6px;border-radius:6px}}@media(max-width:640px){{main{{padding:14px}}}}
</style></head>
<body><header><h1>⚖ Проверка запуска</h1><div>Только обязательные проверки текущего M1/M2 продукта</div></header>
<main>
<section class="card"><h2>{'✅ Базовые настройки заполнены' if data['ok'] else '⚠️ Есть незаполненные обязательные настройки'}</h2>
<p>Версия: <code>{escape(data['version'])}</code> · Среда: <code>{escape(data['env'])}</code> · Платежи: <code>{escape(data['payment_provider'])}</code></p>
<p>BOT: <b>{data['run_bot']}</b> · Scheduler: <b>{data['run_scheduler']}</b></p>
<p>Этот экран не объявляет релиз готовым: runtime CI, миграции и интеграционные проверки должны пройти отдельно.</p></section>
<section class="card"><h2>Проверки конфигурации</h2><table><tbody>{rows}</tbody></table></section>
<section class="card"><h2>Следующие рабочие разделы</h2>{links}</section>
</main></body></html>
"""
    )
