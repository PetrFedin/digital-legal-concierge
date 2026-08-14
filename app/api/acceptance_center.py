from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.superadmin_ui_guards import router as superadmin_ui_guards_router
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["acceptance-center"])
# acceptance_center is mounted before access_management; the personal MFA
# superadmin shell guard therefore owns /access/ui before the legacy route.
router.include_router(superadmin_ui_guards_router)


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_admin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/acceptance-center/status")
async def acceptance_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_admin(request, db, x_admin_token)
    return {
        "ok": True,
        "status": "live_verification_required",
        "checks": [
            {"name": "process_integrity", "url": "/admin/workdesk/integrity"},
            {"name": "health", "url": "/health-center"},
            {"name": "security", "url": "/security-events/status"},
            {"name": "audit_integrity", "url": "/audit-center/integrity"},
        ],
        "message": "Acceptance считается фактом только после выполненных тестов и живых проверок, а не по статической странице.",
    }


@router.get("/acceptance-center/ui")
async def acceptance_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _require_admin(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return RedirectResponse(url="/admin/workdesk/ui", status_code=303)
