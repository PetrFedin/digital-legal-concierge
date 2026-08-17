from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.backup_center import (
    BACKUP_CENTER_HTML,
    _safe_archive,
    _safe_backup_root,
    _verify_restorable_archive,
    backup_inventory,
)
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_SUPERADMIN
from app.security.backup_encryption import BackupSecurityError
from app.security.backup_restore_assessment import BackupRevokedError
from app.security.backup_restore_fence import BackupRestoreFenceError
from app.security.document_access import DocumentAccessError, resolve_document_actor
from app.security.security_events import record_security_event_best_effort

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
    """Authenticate before returning the backup-management HTML shell."""

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
    return HTMLResponse(BACKUP_CENTER_HTML)


@router.get("/backup-center/status")
async def protected_backup_center_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Return backup inventory only after a current MFA superadmin identity resolves."""

    await _superadmin(request, db, x_admin_token)
    result = await asyncio.to_thread(backup_inventory)
    result["version"] = "1.0.0-v41"
    return result


@router.post("/backup-center/verify/{archive_name}")
async def protected_verify_backup_archive(
    archive_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Verify an archive without relying on the legacy broken sync auth helper."""

    actor = await _superadmin(request, db, x_admin_token)
    try:
        root = _safe_backup_root()
    except BackupSecurityError as error:
        raise HTTPException(409, "Каталог резервных копий небезопасен") from error
    candidate = _safe_archive(root, archive_name)
    try:
        metadata = await asyncio.to_thread(
            _verify_restorable_archive,
            candidate,
            root,
        )
        return {
            "ok": True,
            "archive": archive_name,
            **asdict(metadata),
        }
    except BackupRevokedError as error:
        await record_security_event_best_effort(
            action="security.backup_restore_revoked",
            severity="critical",
            source="backup_center",
            actor_id=int(actor.account_id),
            client_address=request.client.host if request.client else None,
            resource_type="backup",
            details={"reason": type(error).__name__, "archive": archive_name},
            comment="Заблокирована проверка отозванной резервной копии",
            sample_seconds=1,
        )
        raise HTTPException(
            409,
            "Резервная копия отозвана политикой криптографического удаления",
        ) from error
    except BackupRestoreFenceError as error:
        await record_security_event_best_effort(
            action="security.backup_restore_policy_unavailable",
            severity="critical",
            source="backup_center",
            actor_id=int(actor.account_id),
            client_address=request.client.host if request.client else None,
            resource_type="backup",
            details={"reason": type(error).__name__, "archive": archive_name},
            comment="Restore-fence не прошёл проверку; операции заблокированы",
            sample_seconds=1,
        )
        raise HTTPException(
            409,
            "Политика восстановления повреждена или недоступна",
        ) from error
    except BackupSecurityError as error:
        await record_security_event_best_effort(
            action="security.backup_verification_failed",
            severity="critical",
            source="backup_center",
            actor_id=int(actor.account_id),
            client_address=request.client.host if request.client else None,
            resource_type="backup",
            details={"reason": type(error).__name__, "archive": archive_name},
            comment="Резервная копия не прошла криптографическую проверку",
            sample_seconds=1,
        )
        raise HTTPException(409, "Резервная копия не прошла проверку целостности") from error


__all__ = ["router"]
