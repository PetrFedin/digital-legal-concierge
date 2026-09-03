from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(prefix="/install-wizard", tags=["install-wizard"])


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


@router.get("")
async def install_wizard_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    steps = [
        {
            "step": 1,
            "title": "BOT_TOKEN",
            "ok": bool(settings.bot_token and settings.bot_token != "CHANGE_ME")
            or not settings.run_bot,
        },
        {"step": 2, "title": "База данных", "ok": bool(settings.database_url)},
        {
            "step": 3,
            "title": "Админ",
            "ok": bool(settings.admin_password or settings.app_env == "local"),
        },
        {
            "step": 4,
            "title": "Платежи",
            "ok": settings.payment_provider == "fake"
            or bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
        },
        {
            "step": 5,
            "title": "Хранилище",
            "ok": Path(settings.storage_dir).exists(),
        },
    ]
    return {
        "ok": all(step["ok"] for step in steps),
        "steps": steps,
        "next": "scripts/production_wizard.py",
    }


@router.get("/ui", response_class=HTMLResponse)
async def install_wizard_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    gate = await _ui_admin_or_redirect(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    return HTMLResponse(INSTALL_HTML)


INSTALL_HTML = """
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Настройка установки</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f6fa;color:#172033;margin:0}.wrap{max-width:880px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e4e7ec;border-radius:16px;padding:18px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.muted{color:#667085;font-size:13px}.button{display:inline-block;background:#3157d5;color:white;text-decoration:none;padding:10px 12px;border-radius:10px;font-weight:700;margin-right:8px}@media(max-width:640px){.grid{grid-template-columns:1fr}.wrap{padding:14px}}</style></head><body><main class="wrap"><h1>⚙ Настройка установки</h1><p class="muted">Этот экран доступен только администратору и ведёт к существующим системным проверкам.</p><div class="grid"><section class="card"><h3>Конфигурация</h3><p class="muted">Telegram, платежи, сроки и параметры продукта.</p><a class="button" href="/settings-ui">Настройки</a></section><section class="card"><h3>Сервис и БД</h3><p class="muted">Доступность приложения и инфраструктуры.</p><a class="button" href="/health-center/ui">Состояние</a></section><section class="card"><h3>Интеграции</h3><p class="muted">Миграции, webhook и внешние зависимости.</p><a class="button" href="/diagnostic-center/ui">Диагностика</a></section><section class="card"><h3>Безопасность</h3><p class="muted">Сессии, ключи и события безопасности.</p><a class="button" href="/security-events/ui">Безопасность</a></section></div><p><a class="button" href="/launch-assistant">Проверка запуска</a><a class="button" href="/operator">Рабочие разделы</a></p></main></body></html>
"""
