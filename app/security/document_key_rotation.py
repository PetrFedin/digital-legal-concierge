from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.security.document_encryption import (
    ENCRYPTION_STATUS,
    DocumentEncryptionError,
    decrypt_file_bytes,
    encrypt_file,
    is_encrypted_file,
    rotate_encrypted_file,
)
from app.security.keyring import document_encryption_ring
from app.security.security_events import record_security_event
from app.storage import LocalStorageService

ENCRYPTION_ERROR_STATUS = "ENCRYPTION_ERROR"


async def migrate_document_encryption(
    db: AsyncSession,
    *,
    limit: int = 50,
) -> dict[str, int]:
    """Encrypt legacy plaintext files and rotate old encrypted containers.

    Migration is in-place. If the database transaction is interrupted after the
    file replacement, the next run recognizes the encrypted header and repairs
    metadata rather than attempting to encrypt ciphertext as plaintext.
    """

    active = document_encryption_ring().require_active()
    documents = (
        await db.execute(
            select(Document)
            .where(
                Document.security_status == "VERIFIED",
                Document.sha256.is_not(None),
                or_(
                    Document.encryption_status != ENCRYPTION_STATUS,
                    Document.encryption_key_id.is_(None),
                    Document.encryption_key_id != active.key_id,
                ),
            )
            .order_by(Document.id.asc())
            .limit(max(1, int(limit)))
            .with_for_update()
        )
    ).scalars().all()

    storage = LocalStorageService()
    result = {"encrypted": 0, "rotated": 0, "repaired": 0, "failed": 0}
    for document in documents:
        old_status = document.encryption_status
        old_key_id = document.encryption_key_id
        try:
            path = storage.resolve_storage_path(document.file_path)
            if is_encrypted_file(path):
                _, current = decrypt_file_bytes(
                    path,
                    expected_sha256=document.sha256,
                )
                if current.key_id != active.key_id:
                    metadata = rotate_encrypted_file(
                        path,
                        expected_sha256=document.sha256,
                    )
                    result["rotated"] += 1
                    action = "security.document_encryption_key_rotated"
                else:
                    metadata = current
                    result["repaired"] += 1
                    action = "security.document_encryption_metadata_repaired"
            else:
                metadata = encrypt_file(
                    path,
                    path,
                    expected_sha256=document.sha256,
                )
                result["encrypted"] += 1
                action = "security.document_encrypted_at_rest"

            document.encryption_status = ENCRYPTION_STATUS
            document.encryption_key_id = metadata.key_id
            document.encrypted_at = metadata.encrypted_at
            document.encryption_error = None
            await record_security_event(
                db,
                action=action,
                severity="info",
                source="document_encryption_migration",
                resource_type="document",
                resource_id=document.id,
                details={
                    "case_id": document.case_id,
                    "old_status": old_status,
                    "old_key_id": old_key_id,
                    "new_key_id": metadata.key_id,
                    "sha256_prefix": str(document.sha256)[:12],
                },
                comment="Хранилище юридического документа приведено к активному ключу",
            )
        except (DocumentEncryptionError, OSError, ValueError) as error:
            result["failed"] += 1
            document.encryption_status = ENCRYPTION_ERROR_STATUS
            document.encryption_error = type(error).__name__[:255]
            await record_security_event(
                db,
                action="security.document_encryption_failed",
                severity="critical",
                source="document_encryption_migration",
                resource_type="document",
                resource_id=document.id,
                details={
                    "case_id": document.case_id,
                    "error_type": type(error).__name__,
                    "sha256_prefix": str(document.sha256 or "")[:12] or None,
                },
                comment="Документ не удалось зашифровать или проверить",
            )
    await db.flush()
    return result
