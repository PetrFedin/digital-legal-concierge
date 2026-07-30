from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.security.document_encryption import (
    ENCRYPTION_STATUS,
    FORMAT_V1,
    FORMAT_V2,
    DocumentEncryptionError,
    encrypt_file,
    encrypted_format_version,
    is_encrypted_file,
    rotate_encrypted_file,
)
from app.security.keyring import document_encryption_ring
from app.security.security_events import record_security_event
from app.storage import LocalStorageService

ENCRYPTION_ERROR_STATUS = "ENCRYPTION_ERROR"


def _apply_metadata(document: Document, metadata) -> None:
    document.encryption_status = ENCRYPTION_STATUS
    document.encryption_key_id = metadata.key_id
    document.encryption_format_version = metadata.format_version
    document.encryption_envelope_id = metadata.envelope_id
    document.encrypted_data_key = metadata.encrypted_data_key
    document.encrypted_data_key_nonce = metadata.encrypted_data_key_nonce
    document.data_key_destroyed_at = None
    document.encrypted_at = metadata.encrypted_at
    document.encryption_error = None


async def migrate_document_encryption(
    db: AsyncSession,
    *,
    limit: int = 50,
) -> dict[str, int]:
    """Migrate plaintext/DLCENC1 files and rewrap old DLCENC2 envelopes.

    New DLCENC2 ciphertext uses a random per-document DEK. Master-key rotation
    only rewraps that DEK in the database and does not rewrite the document file.
    Legacy plaintext and DLCENC1 containers are rewritten once into DLCENC2.
    """

    active = document_encryption_ring().require_active()
    documents = (
        await db.execute(
            select(Document)
            .where(
                Document.security_status == "VERIFIED",
                Document.sha256.is_not(None),
                Document.data_key_destroyed_at.is_(None),
                or_(
                    Document.encryption_status != ENCRYPTION_STATUS,
                    Document.encryption_key_id.is_(None),
                    Document.encryption_key_id != active.key_id,
                    Document.encryption_format_version != FORMAT_V2,
                    Document.encryption_envelope_id.is_(None),
                    Document.encrypted_data_key.is_(None),
                    Document.encrypted_data_key_nonce.is_(None),
                ),
            )
            .order_by(Document.id.asc())
            .limit(max(1, int(limit)))
            .with_for_update()
        )
    ).scalars().all()

    storage = LocalStorageService()
    result = {
        "encrypted": 0,
        "migrated_v1": 0,
        "rewrapped": 0,
        "repaired": 0,
        "failed": 0,
    }
    for document in documents:
        old_status = document.encryption_status
        old_key_id = document.encryption_key_id
        old_format = int(document.encryption_format_version or FORMAT_V1)
        try:
            path = storage.resolve_storage_path(document.file_path)
            file_format = encrypted_format_version(path)
            if file_format == FORMAT_V2:
                before = path.read_bytes()
                metadata = rotate_encrypted_file(
                    path,
                    expected_sha256=document.sha256,
                    encryption_key_id=document.encryption_key_id,
                    encryption_envelope_id=document.encryption_envelope_id,
                    encrypted_data_key=document.encrypted_data_key,
                    encrypted_data_key_nonce=document.encrypted_data_key_nonce,
                )
                if metadata.key_id != old_key_id:
                    result["rewrapped"] += 1
                    action = "security.document_envelope_rewrapped"
                    if path.read_bytes() != before:
                        raise DocumentEncryptionError(
                            "Ротация envelope неожиданно изменила ciphertext"
                        )
                else:
                    result["repaired"] += 1
                    action = "security.document_encryption_metadata_repaired"
            elif file_format == FORMAT_V1:
                metadata = rotate_encrypted_file(
                    path,
                    expected_sha256=document.sha256,
                )
                result["migrated_v1"] += 1
                action = "security.document_encryption_v1_migrated"
            elif not is_encrypted_file(path):
                metadata = encrypt_file(
                    path,
                    path,
                    expected_sha256=document.sha256,
                )
                result["encrypted"] += 1
                action = "security.document_encrypted_at_rest"
            else:
                raise DocumentEncryptionError(
                    "Неизвестный формат защищённого документа"
                )

            _apply_metadata(document, metadata)
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
                    "old_format": old_format,
                    "new_key_id": metadata.key_id,
                    "new_format": metadata.format_version,
                    "envelope_id_prefix": str(metadata.envelope_id or "")[:12] or None,
                    "sha256_prefix": str(document.sha256)[:12],
                },
                comment="Юридический документ приведён к per-document envelope encryption",
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
