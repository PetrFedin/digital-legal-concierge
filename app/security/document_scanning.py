from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.cases.case_history import add_case_history_event
from app.models.document import Document
from app.security.file_uploads import (
    UploadSecurityError,
    inspect_upload,
    quarantine_file,
    safe_filename,
)

RETRYABLE_SECURITY_STATUSES = {"LEGACY_UNVERIFIED", "SCAN_ERROR"}


async def rescan_legacy_documents(db: AsyncSession, *, limit: int = 100) -> dict[str, int]:
    documents = (
        await db.execute(
            select(Document)
            .where(Document.security_status.in_(RETRYABLE_SECURITY_STATUSES))
            .order_by(Document.id.asc())
            .limit(max(1, int(limit)))
            .with_for_update()
        )
    ).scalars().all()

    result = {
        "verified": 0,
        "quarantined": 0,
        "missing": 0,
        "scan_error": 0,
    }
    max_bytes = max(1, int(settings.max_document_upload_mb)) * 1024 * 1024
    for document in documents:
        path = Path(document.file_path)
        if not path.is_file():
            document.security_status = "MISSING"
            document.security_reason = "stored_file_not_found"
            result["missing"] += 1
            await add_case_history_event(
                db,
                actor_type="system",
                actor_id=None,
                case_id=document.case_id,
                action="DOCUMENT_SECURITY_RESCAN_MISSING",
                new_value={"document_id": document.id},
            )
            continue

        try:
            inspection = inspect_upload(
                path,
                original_name=document.file_name,
                claimed_mime=document.mime_type,
                declared_size=document.file_size,
                max_bytes=max_bytes,
            )
        except UploadSecurityError as error:
            quarantine_path = None
            if settings.quarantine_rejected_uploads:
                try:
                    quarantine_path = quarantine_file(
                        path,
                        quarantine_dir=Path(settings.storage_dir) / "quarantine",
                        error=error,
                        safe_name=safe_filename(document.file_name),
                        case_id=document.case_id,
                    )
                except OSError:
                    quarantine_path = None
            if not quarantine_path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            document.file_path = quarantine_path or "quarantined"
            document.sha256 = error.sha256
            document.security_status = "QUARANTINED"
            document.security_reason = error.code[:255]
            result["quarantined"] += 1
            await add_case_history_event(
                db,
                actor_type="system",
                actor_id=None,
                case_id=document.case_id,
                action="DOCUMENT_SECURITY_RESCAN_REJECTED",
                new_value={
                    "document_id": document.id,
                    "reason_code": error.code,
                    "sha256": error.sha256,
                },
            )
        except Exception as error:
            document.security_status = "SCAN_ERROR"
            document.security_reason = type(error).__name__[:255]
            result["scan_error"] += 1
        else:
            document.file_name = inspection.safe_name
            document.mime_type = inspection.mime_type
            document.file_size = inspection.size_bytes
            document.sha256 = inspection.sha256
            document.detected_type = inspection.detected_type
            document.security_status = "VERIFIED"
            document.security_reason = None
            document.scanned_at = inspection.scanned_at
            result["verified"] += 1
            await add_case_history_event(
                db,
                actor_type="system",
                actor_id=None,
                case_id=document.case_id,
                action="DOCUMENT_SECURITY_RESCAN_VERIFIED",
                new_value={
                    "document_id": document.id,
                    "sha256": inspection.sha256,
                    "detected_type": inspection.detected_type,
                },
            )

    if documents:
        await db.flush()
    return result
