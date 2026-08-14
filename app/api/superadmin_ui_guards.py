from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.access_management import ACCESS_HTML
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["superadmin-ui-guards"])


@router.get("/access/ui", response_class=HTMLResponse)
async def protected_access_management_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    try:
        actor = await resolve_document_actor(db, token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login?next=/access/ui", status_code=303)
        raise
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(status_code=403, detail="Доступ только для суперадминистратора")
    # AdminSessionGuardMiddleware has already enforced active account, current
    # role/session_version, token revocation and MFA for this personal session.
    return HTMLResponse(ACCESS_HTML)
