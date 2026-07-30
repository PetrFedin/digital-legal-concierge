from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_history import add_case_history_event
from app.models.document import Document
from app.models.document_access_grant import DocumentAccessGrant
from app.security.document_access import (
    DocumentAccessError,
    consume_document_grant,
    grant_cookie_name,
    grant_download_path,
    issue_document_grant,
    load_authorized_document,
    resolve_document_actor,
)
from app.security.security_events import (
    record_security_event,
    record_security_event_best_effort,
)
from app.storage import LocalStorageService

router = APIRouter(prefix="/document-access", tags=["document-access"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


def _client_address(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _record_denial(
    request: Request,
    error: DocumentAccessError,
    *,
    actor_id: int | None = None,
    document_id: int | None = None,
    public_id: str | None = None,
) -> None:
    await record_security_event_best_effort(
        action="security.document_access_denied",
        severity="warning",
        source="document_access_api",
        actor_id=actor_id,
        client_address=_client_address(request),
        resource_type="document",
        resource_id=document_id,
        details={
            "reason": error.reason,
            "status_code": error.status_code,
            "grant_ref": public_id,
            "path": request.url.path,
        },
        comment="Отклонён запрос доступа к защищённому документу",
        sample_seconds=1,
    )


@router.get("/cases/{case_id}/documents")
async def list_authorized_case_documents(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = None
    try:
        actor = await resolve_document_actor(db, _token(request, x_admin_token))
        documents = (
            await db.execute(
                select(Document)
                .where(Document.case_id == case_id)
                .order_by(Document.created_at.desc())
            )
        ).scalars().all()
        result = []
        for document in documents:
            try:
                _, case = await load_authorized_document(
                    db,
                    actor=actor,
                    document_id=document.id,
                )
            except DocumentAccessError as error:
                if error.reason == "lawyer_not_assigned":
                    raise
                continue
            result.append(
                {
                    "document_id": document.id,
                    "case_id": case.id,
                    "title": document.title,
                    "file_name": document.file_name,
                    "document_type": document.document_type,
                    "mime_type": document.mime_type,
                    "file_size": document.file_size,
                    "version": document.version,
                    "status": document.status,
                    "encrypted": True,
                    "encryption_format_version": document.encryption_format_version,
                    "created_at": document.created_at,
                }
            )
        return result
    except DocumentAccessError as error:
        await db.rollback()
        await _record_denial(
            request,
            error,
            actor_id=actor.account_id if actor else None,
        )
        raise


@router.post("/documents/{document_id}/grant")
async def create_document_download_grant(
    document_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = None
    try:
        actor = await resolve_document_actor(db, _token(request, x_admin_token))
        document, case = await load_authorized_document(
            db,
            actor=actor,
            document_id=document_id,
        )
        issued = await issue_document_grant(
            db,
            actor=actor,
            document=document,
            case=case,
            client_address=_client_address(request),
        )
        await add_case_history_event(
            db,
            actor_type="admin_user",
            actor_id=actor.account_id,
            case_id=case.id,
            action="DOCUMENT_DOWNLOAD_GRANT_CREATED",
            new_value={
                "document_id": document.id,
                "grant_id": issued.grant.public_id,
                "expires_at": issued.grant.expires_at.isoformat(),
                "actor_role": actor.role,
            },
        )
        await db.commit()

        download_path = grant_download_path(issued.grant.public_id)
        response = JSONResponse(
            {
                "ok": True,
                "document_id": document.id,
                "download_url": download_path,
                "expires_at": issued.grant.expires_at.isoformat(),
                "one_time": True,
            }
        )
        response.set_cookie(
            grant_cookie_name(issued.grant.public_id),
            issued.secret,
            max_age=min(max(int(settings.document_access_grant_ttl_seconds), 30), 300),
            httponly=True,
            secure=settings.app_env == "production",
            samesite="strict",
            path=download_path,
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    except DocumentAccessError as error:
        await db.rollback()
        await _record_denial(
            request,
            error,
            actor_id=actor.account_id if actor else None,
            document_id=document_id,
        )
        raise


@router.get("/grants/{public_id}/download")
async def download_document_once(
    public_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = None
    cookie_name = grant_cookie_name(public_id)
    download_path = grant_download_path(public_id)
    try:
        actor = await resolve_document_actor(db, _token(request, x_admin_token))
        grant, document, case = await consume_document_grant(
            db,
            actor=actor,
            public_id=public_id,
            secret=request.cookies.get(cookie_name),
        )
        try:
            content = LocalStorageService().read_document_bytes(
                document.file_path,
                expected_sha256=document.sha256,
                encryption_key_id=document.encryption_key_id,
                encryption_envelope_id=document.encryption_envelope_id,
                encrypted_data_key=document.encrypted_data_key,
                encrypted_data_key_nonce=document.encrypted_data_key_nonce,
            )
        except Exception as error:
            await record_security_event(
                db,
                action="security.document_decryption_failed",
                severity="critical",
                source="document_access_api",
                actor_id=actor.account_id,
                client_address=_client_address(request),
                resource_type="document",
                resource_id=document.id,
                details={"reason": type(error).__name__, "grant_ref": public_id},
                comment="Не удалось расшифровать документ для авторизованной выдачи",
            )
            await db.commit()
            raise DocumentAccessError(
                500,
                "Не удалось безопасно подготовить документ",
                "document_decryption_failed",
            ) from error

        await add_case_history_event(
            db,
            actor_type="admin_user",
            actor_id=actor.account_id,
            case_id=case.id,
            action="DOCUMENT_DOWNLOADED",
            new_value={
                "document_id": document.id,
                "grant_id": grant.public_id,
                "actor_role": actor.role,
                "one_time": True,
            },
        )
        await db.commit()

        safe_name = str(document.file_name or "document").replace("\r", "").replace("\n", "")
        response = Response(
            content=content,
            media_type=document.mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename=document; filename*=UTF-8''{quote(safe_name)}",
                "Cache-Control": "no-store, private, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
                "Referrer-Policy": "no-referrer",
            },
        )
        response.delete_cookie(cookie_name, path=download_path)
        return response
    except DocumentAccessError as error:
        await db.rollback()
        await _record_denial(
            request,
            error,
            actor_id=actor.account_id if actor else None,
            public_id=public_id,
        )
        response = JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail},
        )
        response.delete_cookie(cookie_name, path=download_path)
        return response


@router.delete("/grants/{public_id}")
async def revoke_document_grant(
    public_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = None
    try:
        actor = await resolve_document_actor(db, _token(request, x_admin_token))
        grant = (
            await db.execute(
                select(DocumentAccessGrant).where(DocumentAccessGrant.public_id == public_id)
            )
        ).scalar_one_or_none()
        if not grant or grant.actor_account_id != actor.account_id:
            raise DocumentAccessError(404, "Разрешение не найдено", "grant_not_found")
        if grant.used_at is None and grant.revoked_at is None:
            from datetime import datetime, timezone

            grant.revoked_at = datetime.now(timezone.utc)
        await db.commit()
        return {"ok": True, "grant_id": public_id, "revoked": True}
    except DocumentAccessError as error:
        await db.rollback()
        await _record_denial(
            request,
            error,
            actor_id=actor.account_id if actor else None,
            public_id=public_id,
        )
        raise
