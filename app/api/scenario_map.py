from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["scenario-map"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/scenario-map")
async def scenario_map(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    return {
        "ok": True,
        "status": "live_e2e_is_source_of_truth",
        "note": (
            "Статическая карта v19 удалена: она расходилась с текущими proof-bearing "
            "M1/M2 переходами. Проверяйте живое состояние и фактический следующий шаг."
        ),
        "workdesk": "/admin/workdesk/ui",
        "process_integrity": "/admin/workdesk/integrity",
        "lawyer_workspace": "/lawyer/workspace/ui",
        "consultation_control": "/admin/consultation-outcomes/ui",
        "schedule": "/consultation-slots/ui",
    }


@router.get("/scenario-map-ui")
async def scenario_map_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _admin(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return RedirectResponse(url="/admin/workdesk/ui", status_code=303)
