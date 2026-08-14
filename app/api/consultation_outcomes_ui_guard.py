from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consultation_outcomes import OUTCOMES_HTML
from app.api.guided_consultation_outcomes import _inject_client_no_show_ui
from app.api.legacy_consultation_outcome_guard import (
    inject_legacy_outcome_ui,
    router as legacy_consultation_outcome_router,
)
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["consultation-outcomes-ui-guard"])
# This guard is included by initial_setup_wizard before the historical
# consultation-outcome routers in app.main. Register legacy recovery here so
# its dedicated API is available without reopening the generic status switch.
router.include_router(legacy_consultation_outcome_router)


@router.get("/admin/consultation-outcomes/ui", response_class=HTMLResponse)
async def consultation_outcomes_ui_guard(
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
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    html = _inject_client_no_show_ui(OUTCOMES_HTML)
    return HTMLResponse(inject_legacy_outcome_ui(html))
