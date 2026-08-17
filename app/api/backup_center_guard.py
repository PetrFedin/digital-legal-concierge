from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.backup_manager import BACKUP_UI, _status_payload, verify_backup_override
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["backup-center-guard"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _superadmin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(status_code=403, detail="Доступ только для суперадминистратора")
    return actor


@router.get("/backup-center/ui", response_class=HTMLResponse)
async def protected_backup_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Authenticate before returning the hardened backup-management shell."""

    try:
        await _superadmin(request, db, x_admin_token)
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
    # Keep the newer backup-manager presentation and its restore-fence wording;
    # the early guard only closes authorization/recovery gaps, it must not
    # downgrade the operator experience to the older legacy template.
    return HTMLResponse(BACKUP_UI)


@router.get("/backup-center/status")
async def protected_backup_center_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Return the sanitized v46 inventory after current MFA-superadmin auth."""

    await _superadmin(request, db, x_admin_token)
    return await _status_payload()


@router.post("/backup-center/verify/{archive_name}")
async def protected_verify_backup_archive(
    archive_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Keep the hardened verification/error audit path behind the early guard."""

    await _superadmin(request, db, x_admin_token)
    return await verify_backup_override(
        archive_name=archive_name,
        request=request,
        db=db,
        x_admin_token=_token(request, x_admin_token),
    )


__all__ = ["router"]
