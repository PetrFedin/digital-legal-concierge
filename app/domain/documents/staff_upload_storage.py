from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterable
from pathlib import Path

from app.config import settings
from app.security.document_encryption import ENCRYPTION_STATUS, FORMAT_V2, encrypt_file
from app.security.file_uploads import (
    UploadSecurityError,
    inspect_upload,
    preflight_upload,
    quarantine_file,
)
from app.storage import LocalStorageService, StoredFile


async def save_staff_upload(
    *,
    chunks: AsyncIterable[bytes],
    case_id: int,
    original_name: str,
    mime_type: str | None,
    declared_size: int | None = None,
) -> StoredFile:
    """Stream a staff upload through the same validation/encryption invariants.

    The HTTP layer never writes client/staff-controlled names into the storage
    tree and never buffers the whole document in memory. Bytes first land in an
    isolated random incoming file, are size-bounded, content-inspected, then
    envelope-encrypted into the case directory. The database-facing path is a
    portable ``cases/<case_id>/<random>.dlcenc`` key, never a host-specific
    absolute filesystem path.
    """

    storage = LocalStorageService()
    preflight = preflight_upload(
        original_name=original_name,
        claimed_mime=mime_type,
        declared_size=declared_size,
        max_bytes=storage.max_upload_bytes,
    )
    temporary = storage.incoming_dir / f"{uuid.uuid4().hex}.staff-upload"
    target: Path | None = None
    total = 0
    try:
        with temporary.open("xb") as destination:
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            async for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > storage.max_upload_bytes:
                    raise UploadSecurityError(
                        "file_too_large",
                        f"Файл слишком большой. Максимальный размер — {storage.max_upload_bytes // (1024 * 1024)} МБ.",
                    )
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())

        inspection = inspect_upload(
            temporary,
            original_name=preflight.safe_name,
            claimed_mime=mime_type,
            declared_size=total,
            max_bytes=storage.max_upload_bytes,
        )
        case_dir = storage.base_dir / "cases" / str(int(case_id))
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            case_dir.chmod(0o700)
        except OSError:
            pass

        target = case_dir / f"{uuid.uuid4().hex}.dlcenc"
        encryption = encrypt_file(
            temporary,
            target,
            expected_sha256=inspection.sha256,
        )
        temporary.unlink(missing_ok=True)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return StoredFile(
            original_name=inspection.safe_name,
            storage_path=storage.storage_key_for_case_path(target, case_id=case_id),
            mime_type=inspection.mime_type,
            file_size=inspection.size_bytes,
            sha256=inspection.sha256,
            detected_type=inspection.detected_type,
            security_status="VERIFIED",
            scanned_at=inspection.scanned_at,
            encryption_status=ENCRYPTION_STATUS,
            encryption_key_id=encryption.key_id,
            encryption_format_version=FORMAT_V2,
            encryption_envelope_id=encryption.envelope_id,
            encrypted_data_key=encryption.encrypted_data_key,
            encrypted_data_key_nonce=encryption.encrypted_data_key_nonce,
            encrypted_at=encryption.encrypted_at,
        )
    except UploadSecurityError as error:
        if temporary.exists():
            if settings.quarantine_rejected_uploads:
                try:
                    error.quarantine_path = quarantine_file(
                        temporary,
                        quarantine_dir=storage.base_dir / "quarantine",
                        error=error,
                        safe_name=preflight.safe_name,
                        case_id=case_id,
                    )
                except OSError:
                    temporary.unlink(missing_ok=True)
            else:
                temporary.unlink(missing_ok=True)
        raise
    except Exception:
        temporary.unlink(missing_ok=True)
        if target is not None and target.exists():
            target.unlink(missing_ok=True)
        raise


__all__ = ["save_staff_upload"]
