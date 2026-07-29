from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aiogram import Bot

from app.config import settings
from app.security.file_uploads import (
    UploadSecurityError,
    inspect_upload,
    preflight_upload,
    quarantine_file,
    safe_filename,
)


@dataclass(frozen=True)
class StoredFile:
    original_name: str
    storage_path: str
    mime_type: str | None
    file_size: int | None
    sha256: str | None = None
    detected_type: str | None = None
    security_status: str = "VERIFIED"
    scanned_at: datetime | None = None


class LocalStorageService:
    """Local storage with an isolated incoming area and content verification.

    Client-controlled names are never used as storage keys. A file becomes
    visible in a case directory only after size, signature and format checks
    complete successfully. Rejected downloaded content is moved into an
    inaccessible quarantine area for short-lived incident analysis.
    """

    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or settings.storage_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.incoming_dir = self.base_dir / ".incoming"
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.incoming_dir.chmod(0o700)
        except OSError:
            pass

    @property
    def max_upload_bytes(self) -> int:
        return max(1, int(settings.max_document_upload_mb)) * 1024 * 1024

    async def save_telegram_file(
        self,
        *,
        bot: Bot,
        telegram_file_id: str,
        case_id: int,
        original_name: str,
        mime_type: str | None = None,
        file_size: int | None = None,
    ) -> StoredFile:
        preflight = preflight_upload(
            original_name=original_name,
            claimed_mime=mime_type,
            declared_size=file_size,
            max_bytes=self.max_upload_bytes,
        )
        temporary = self.incoming_dir / f"{uuid.uuid4().hex}.upload"
        try:
            telegram_file = await bot.get_file(telegram_file_id)
            await bot.download_file(telegram_file.file_path, destination=temporary)
            inspection = inspect_upload(
                temporary,
                original_name=preflight.safe_name,
                claimed_mime=mime_type,
                declared_size=file_size,
                max_bytes=self.max_upload_bytes,
            )

            case_dir = self.base_dir / "cases" / str(int(case_id))
            case_dir.mkdir(parents=True, exist_ok=True)
            try:
                case_dir.chmod(0o700)
            except OSError:
                pass

            # The storage key contains no user-controlled data. A deterministic
            # digest key also prevents duplicate bytes from consuming space.
            target = case_dir / f"{inspection.sha256}{inspection.extension}"
            if target.exists():
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, target)
                try:
                    target.chmod(0o600)
                except OSError:
                    pass

            return StoredFile(
                original_name=inspection.safe_name,
                storage_path=str(target),
                mime_type=inspection.mime_type,
                file_size=inspection.size_bytes,
                sha256=inspection.sha256,
                detected_type=inspection.detected_type,
                security_status="VERIFIED",
                scanned_at=inspection.scanned_at,
            )
        except UploadSecurityError as error:
            if temporary.exists():
                if settings.quarantine_rejected_uploads:
                    try:
                        error.quarantine_path = quarantine_file(
                            temporary,
                            quarantine_dir=self.base_dir / "quarantine",
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
            raise


__all__ = ["LocalStorageService", "StoredFile", "safe_filename"]
