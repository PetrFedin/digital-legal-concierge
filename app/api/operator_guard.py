from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.refund_resolution_guard import router as refund_resolution_guard_router
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["operator-guard"])


@router.get("/operator")
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
        raise
    if actor.role not in {ROLE_LAWYER, ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для сотрудников")
    target = "/lawyer/workspace/ui" if actor.role == ROLE_LAWYER else "/admin/workdesk/ui"
    return RedirectResponse(url=target, status_code=303)


# operator_guard is mounted by initial_setup_wizard before the legacy refund
# router. Keep the M2 refund lifecycle override in this early staff layer.
router.include_router(refund_resolution_guard_router)
