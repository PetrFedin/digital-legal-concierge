from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.access_role_guard import router as access_role_guard_router
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


@dataclass(frozen=True)
class StaffLandingProblem:
    detail: str


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
        if error.status_code in {403, 409}:
            return StaffLandingProblem(str(error.detail))
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return StaffLandingProblem(str(error.detail))
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
        if error.reason == "role_denied":
            return StaffLandingProblem(
                "Для этой роли рабочий кабинет продукта не назначен."
            )
        raise
    except HTTPException as error:
        # Historical lawyer accounts can be valid staff sessions while their
        # Lawyer business profile is missing, inactive or not linked yet. Do
        # not leave a successful login on a raw JSON 403/409 screen: explain
        # the prerequisite and provide a safe way out without fabricating
        # permissions or a lawyer assignment.
        if error.status_code in {403, 409}:
            return StaffLandingProblem(str(error.detail))
        raise


def _staff_landing_problem_html(problem: StaffLandingProblem) -> str:
    detail = escape(problem.detail or "Рабочий кабинет для учётной записи пока недоступен.")
    return f"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Нужно настроить доступ</title>
<style>
:root{{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--amber:#a15c00;--amber-bg:#fff7e6}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:linear-gradient(145deg,#eef2ff,#f7f8fb 48%,#eef4ff);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}}
.card{{width:min(560px,96vw);background:var(--card);border:1px solid var(--line);border-radius:20px;padding:26px;box-shadow:0 18px 50px rgba(16,24,40,.11)}}
h1{{margin:0 0 8px;font-size:24px}}p{{line-height:1.55}}.detail{{background:var(--amber-bg);border:1px solid #fedf89;border-radius:13px;padding:12px;color:#7a4700}}.muted{{color:var(--muted);font-size:13px}}button,a.button{{display:inline-block;border:0;border-radius:11px;padding:11px 14px;background:var(--blue);color:#fff;font-weight:800;text-decoration:none;cursor:pointer}}form{{margin:16px 0 0}}
</style>
</head>
<body>
<main class="card">
  <h1>⚖ Нужно настроить рабочий доступ</h1>
  <p>Вход выполнен, но система не может безопасно открыть рабочий кабинет для этой учётной записи.</p>
  <div class="detail">{detail}</div>
  <p class="muted">Суперадминистратору нужно проверить базовую роль сотрудника и, для юриста, связь персональной учётной записи с активной карточкой юриста. Система не назначает права автоматически и не подменяет юридическую ответственность.</p>
  <form method="post" action="/logout"><button type="submit">Выйти и войти другой учётной записью</button></form>
</main>
</body>
</html>
"""


def _legacy_admin_ui_recovery(actor):
    """Route old admin-only UI bookmarks without trapping another staff role."""

    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    if isinstance(actor, StaffLandingProblem):
        return RedirectResponse(url="/admin-ui", status_code=303)
    return None


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
    if isinstance(actor, StaffLandingProblem):
        return HTMLResponse(_staff_landing_problem_html(actor), status_code=403)
    if actor.role in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        return RedirectResponse(url="/admin/workdesk/ui", status_code=303)
    if actor.role == ROLE_LAWYER:
        return RedirectResponse(url="/lawyer/workspace/ui", status_code=303)
    return HTMLResponse(
        _staff_landing_problem_html(
            StaffLandingProblem("Для этой роли рабочий кабинет продукта не назначен.")
        ),
        status_code=403,
    )


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
    recovery = _legacy_admin_ui_recovery(actor)
    if recovery is not None:
        return recovery
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
    recovery = _legacy_admin_ui_recovery(actor)
    if recovery is not None:
        return recovery
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
    recovery = _legacy_admin_ui_recovery(actor)
    if recovery is not None:
        return recovery
    return RedirectResponse(url="/admin/workdesk/ui", status_code=303)


# Mounted before legacy staff/workdesk/readiness routers in app.main. Exact
# production endpoints below own the authenticated entrypoints.
router.include_router(access_role_guard_router)
router.include_router(operator_guard_router)
router.include_router(consultation_outcomes_ui_guard_router)
router.include_router(workdesk_integrity_guard_router)