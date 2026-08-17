from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consultation_outcomes_ui_guard import router as consultation_outcomes_ui_guard_router
from app.api.operator_guard import router as operator_guard_router
from app.api.workdesk_integrity_guard import router as workdesk_integrity_guard_router
from app.config import settings
from app.db.session import get_db
from app.domain.payments.mode import payment_mode_valid
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor
from app.security.keyring import security_key_status

router = APIRouter(tags=["initial-setup"])
VERSION = "1.0.0-v46"


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_admin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


async def _admin_ui_or_login(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    try:
        return await _require_admin(request, db, header_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return None
        raise


async def _staff_ui_or_login(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    try:
        return await resolve_document_actor(db, _token(request, header_token))
    except DocumentAccessError as error:
        if error.status_code == 401:
            return None
        raise


@router.get("/health")
async def public_liveness_probe():
    return {"ok": True, "version": VERSION}


@router.get("/ready")
async def public_readiness_probe(db: AsyncSession = Depends(get_db)):
    db_ready = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_ready = False
    bot_ready = (not settings.run_bot) or bool(
        settings.bot_token and settings.bot_token != "CHANGE_ME"
    )
    storage_ready = Path(settings.storage_dir).exists()
    keys_ready = bool(security_key_status().get("ok"))
    ready = bool(
        db_ready
        and bot_ready
        and storage_ready
        and payment_mode_valid()
        and keys_ready
    )
    payload = {"ok": ready, "version": VERSION}
    if not ready:
        return JSONResponse(status_code=503, content=payload)
    return payload


@router.get("/launch-check")
async def protected_launch_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return {
        "ok": True,
        "status": "live_verification_required",
        "version": VERSION,
        "workdesk": "/admin/workdesk/ui",
        "process_integrity": "/admin/workdesk/integrity",
        "settings": "/settings-ui",
        "health": "/health-center/ui",
        "diagnostics": "/diagnostic-center/ui",
        "security": "/security-events/ui",
        "audit": "/audit-center/ui",
        "backup": "/backup-center/ui",
        "retention": "/retention/ui",
    }


@router.get("/admin-ui")
async def legacy_admin_ui_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _staff_ui_or_login(request, db, x_admin_token)
    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    if actor.role in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        return RedirectResponse(url="/admin/workdesk/ui", status_code=303)
    if actor.role == ROLE_LAWYER:
        return RedirectResponse(url="/lawyer/workspace/ui", status_code=303)
    raise HTTPException(status_code=403, detail="Для роли не настроен рабочий кабинет")


@router.get("/initial-setup-wizard/status")
async def setup_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return {
        "ok": True,
        "status": "managed_in_admin_settings",
        "settings": "/settings-ui",
        "workdesk": "/admin/workdesk/ui",
        "diagnostics": "/diagnostic-center/ui",
    }


@router.get("/initial-setup-wizard/ui")
async def setup_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin_ui_or_login(request, db, x_admin_token)
    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/settings-ui", status_code=303)


@router.get("/install-wizard")
async def install_wizard_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return {
        "ok": True,
        "status": "live_checks_required",
        "settings": "/settings-ui",
        "health": "/health-center/ui",
        "diagnostics": "/diagnostic-center/ui",
        "security": "/security-events/ui",
    }


@router.get("/install-wizard/ui")
async def install_wizard_ui_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin_ui_or_login(request, db, x_admin_token)
    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/diagnostic-center/ui", status_code=303)


@router.get("/launch-assistant/status")
async def launch_assistant_status_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return {
        "ok": True,
        "status": "live_checks_required",
        "workdesk": "/admin/workdesk/ui",
        "process_integrity": "/admin/workdesk/integrity",
        "health": "/health-center/ui",
        "diagnostics": "/diagnostic-center/ui",
        "security": "/security-events/ui",
        "audit": "/audit-center/ui",
    }


@router.get("/launch-assistant")
async def launch_assistant_ui_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin_ui_or_login(request, db, x_admin_token)
    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/admin/workdesk/ui", status_code=303)


# Mounted before legacy staff/workdesk/readiness routers in app.main. Exact
# production endpoints below own the authenticated entrypoints.
router.include_router(operator_guard_router)
router.include_router(consultation_outcomes_ui_guard_router)
router.include_router(workdesk_integrity_guard_router)
