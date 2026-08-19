from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.access_role_guard import router as access_role_guard_router
from app.api.consultation_outcomes_ui_guard import router as consultation_outcomes_ui_guard_router
from app.api.operator_guard import router as operator_guard_router
from app.api.workdesk_integrity_guard import router as workdesk_integrity_guard_router
from app.config import settings
from app.db.session import get_db
from app.domain.payments.mode import payment_mode_valid
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor
from app.security.keyring import security_key_status

router = APIRouter(tags=["initial-setup"])
VERSION = "1.0.0-v46"


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_admin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await resolve_document_actor(db, _token(request, header_token))
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
            return RedirectResponse(url="/operator", status_code=303)
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/operator", status_code=303)
        raise


async def _db_status(db: AsyncSession) -> tuple[bool, str]:
    try:
        await db.execute(text("SELECT 1"))
        return True, "База данных отвечает"
    except Exception as error:
        return False, f"База данных недоступна: {type(error).__name__}"


def _payment_status() -> tuple[bool, str]:
    if not payment_mode_valid():
        return False, "PAYMENT_PROVIDER содержит неподдерживаемое значение"
    provider = str(settings.payment_provider or "").strip().lower()
    if provider == "disabled":
        return True, "Онлайн-оплата отключена конфигурацией"
    if provider == "fake":
        if settings.app_env == "production":
            return False, "FAKE-платежи запрещены для production"
        return True, "FAKE-провайдер разрешён только для непроизводственной среды"
    if provider == "yookassa":
        if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
            return False, "Для ЮKassa не заданы shop_id/secret_key"
        return True, "ЮKassa сконфигурирована"
    return False, "Платёжный режим не определён"


def _storage_status() -> tuple[bool, str]:
    path = Path(settings.storage_dir)
    if not path.exists():
        return False, "Каталог хранилища документов не создан"
    if not path.is_dir():
        return False, "Путь хранилища документов не является каталогом"
    return True, "Хранилище документов доступно"


def _security_status() -> tuple[bool, str]:
    status = security_key_status()
    failures = [str(item) for item in status.get("failures") or []]
    if failures:
        return False, "; ".join(failures[:3])
    return True, "Ключевой контур прошёл конфигурационную проверку"


async def _snapshot(db: AsyncSession) -> dict[str, object]:
    db_ok, db_detail = await _db_status(db)
    payment_ok, payment_detail = _payment_status()
    storage_ok, storage_detail = _storage_status()
    security_ok, security_detail = _security_status()
    checks = [
        {
            "code": "database",
            "label": "База данных",
            "ok": db_ok,
            "detail": db_detail,
            "href": "/health-center/ui",
        },
        {
            "code": "storage",
            "label": "Хранилище документов",
            "ok": storage_ok,
            "detail": storage_detail,
            "href": "/diagnostic-center/ui",
        },
        {
            "code": "payments",
            "label": "Платёжный контур",
            "ok": payment_ok,
            "detail": payment_detail,
            "href": "/settings-ui",
        },
        {
            "code": "security",
            "label": "Ключи и безопасность",
            "ok": security_ok,
            "detail": security_detail,
            "href": "/security-events/ui",
        },
        {
            "code": "telegram",
            "label": "Telegram",
            "ok": bool(settings.bot_token and settings.bot_token != "CHANGE_ME")
            or not settings.run_bot,
            "detail": (
                "BOT_TOKEN задан"
                if settings.run_bot
                else "Запуск Telegram-бота отключён конфигурацией"
            ),
            "href": "/diagnostic-center/ui",
        },
    ]
    return {
        "ok": all(bool(item["ok"]) for item in checks),
        "version": VERSION,
        "environment": settings.app_env,
        "checks": checks,
    }


@router.get("/launch-check")
async def launch_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Authenticated compact readiness projection for setup screens."""

    await _require_admin(request, db, x_admin_token)
    return await _snapshot(db)


@router.get("/initial-setup-wizard/status")
async def initial_setup_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return await _snapshot(db)


@router.get("/initial-setup-wizard/ui", response_class=HTMLResponse)
async def initial_setup_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    gate = await _ui_admin_or_redirect(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    data = await _snapshot(db)
    rows = "".join(
        (
            "<article class='item'>"
            f"<div><b>{escape(str(item['label']))}</b>"
            f"<p>{escape(str(item['detail']))}</p></div>"
            f"<span class='badge {'ok' if item['ok'] else 'bad'}'>{'Готово' if item['ok'] else 'Проверить'}</span>"
            f"<a href='{escape(str(item['href']), quote=True)}'>Открыть</a>"
            "</article>"
        )
        for item in data["checks"]
    )
    return HTMLResponse(
        f"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Проверка обязательной настройки</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f6fa;color:#172033;margin:0}}main{{max-width:900px;margin:auto;padding:24px}}.summary,.item{{background:#fff;border:1px solid #e4e7ec;border-radius:16px;padding:16px;margin:10px 0}}.item{{display:grid;grid-template-columns:1fr auto auto;gap:12px;align-items:center}}.item p{{margin:5px 0 0;color:#667085;font-size:13px}}.badge{{border-radius:999px;padding:6px 9px;font-size:12px;font-weight:800}}.ok{{background:#ecfdf3;color:#067647}}.bad{{background:#fef3f2;color:#b42318}}a{{background:#3157d5;color:#fff;text-decoration:none;border-radius:9px;padding:8px 10px;font-weight:700;font-size:13px}}@media(max-width:640px){{main{{padding:14px}}.item{{grid-template-columns:1fr}}}}</style></head><body><main><h1>Обязательная настройка</h1><section class="summary"><b>{'Базовая конфигурация заполнена' if data['ok'] else 'Есть обязательные пункты для проверки'}</b><p>Версия {escape(str(data['version']))} · среда {escape(str(data['environment']))}. Этот экран не заменяет runtime CI и миграционный прогон.</p></section>{rows}<p><a href="/operator">Рабочие разделы</a> <a href="/launch-assistant">Проверка запуска</a></p></main></body></html>
"""
    )


# Temporary compatibility mounts. They no longer own /health, /ready,
# /operator, /admin-ui, /install-wizard or /launch-assistant. Each remaining
# public path is being consolidated into one canonical module.
router.include_router(access_role_guard_router)
router.include_router(operator_guard_router)
router.include_router(consultation_outcomes_ui_guard_router)
router.include_router(workdesk_integrity_guard_router)

__all__ = ["router"]
