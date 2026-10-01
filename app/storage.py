from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aiogram import Bot

from app.config import settings
from app.security.document_encryption import (
    ENCRYPTION_STATUS,
    FORMAT_V2,
    DocumentEncryptionError,
    decrypt_file_bytes,
    encrypt_file,
)
from app.security.file_uploads import (
    UploadSecurityError,
    inspect_upload,
    preflight_upload,
    quarantine_file,
    safe_filename,
    validate_downloaded_size,
)
from app.security.malware_scanning import (
    MalwareScanner,
    assert_malware_scan_admitted,
    malware_scanner_from_settings,
)


_DOCUMENT_CIPHERTEXT_NAME = re.compile(r"^[0-9a-fA-F]{32}\.dlcenc$")


@dataclass(frozen=True)
class StoredFile:
    original_name: str
    storage_path: str
    mime_type: str | None
    file_size: int | None
    sha256: str | None = None
    detected_type: str | None = None
    security_status: str = "VERIFIED"
    security_reason: str | None = None
    scanned_at: datetime | None = None
    encryption_status: str = ENCRYPTION_STATUS
    encryption_key_id: str | None = None
    encryption_format_version: int = FORMAT_V2
    encryption_envelope_id: str | None = None
    encrypted_data_key: str | None = None
    encrypted_data_key_nonce: str | None = None
    encrypted_at: datetime | None = None


class LocalStorageService:
    """Verified local storage with authenticated envelope encryption at rest.

    Database rows use portable storage keys such as
    ``cases/<case_id>/<random>.dlcenc`` rather than host-specific absolute
    filesystem paths. Legacy absolute rows are accepted only when they end in
    that exact case-storage shape; they are then *rebased* onto the currently
    configured storage root and are never read from the legacy absolute root.

    Relative document keys are stricter: they must be exactly the canonical
    three-component form. A prefixed relative value such as
    ``tmp/cases/<id>/<file>`` is never silently truncated to a valid key.

    Client-controlled names are never used as storage keys. Incoming bytes are
    isolated, validated, encrypted with a unique per-document data key and only
    then moved into a case directory. The wrapped data key is returned to the
    database layer and is deliberately not embedded in the encrypted file.
    """

    def __init__(
        self,
        base_dir: str | None = None,
        *,
        malware_scanner: MalwareScanner | None = None,
    ):
        requested = Path(base_dir or settings.storage_dir)
        if requested.is_symlink():
            raise DocumentEncryptionError(
                "Корень защищённого хранилища не может быть символической ссылкой"
            )
        self.base_dir = requested.resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.incoming_dir = self.base_dir / ".incoming"
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.malware_scanner = malware_scanner or malware_scanner_from_settings()
        try:
            self.base_dir.chmod(0o700)
            self.incoming_dir.chmod(0o700)
        except OSError:
            pass

    @property
    def max_upload_bytes(self) -> int:
        return max(1, int(settings.max_document_upload_mb)) * 1024 * 1024

    @staticmethod
    def _portable_document_key(
        storage_path: str | Path,
        *,
        expected_case_id: int | None = None,
    ) -> Path | None:
        """Return the canonical portable key for a document path when present.

        Absolute legacy values are deliberately reduced to their final
        ``cases/<id>/<ciphertext>`` suffix. Any earlier host/root components are
        ignored rather than followed. Relative values must already be exactly
        the canonical key and are never suffix-normalized.
        """

        raw = str(storage_path or "").strip()
        if not raw:
            raise DocumentEncryptionError("Путь документа не задан")
        candidate = Path(raw)
        if ".." in candidate.parts:
            raise DocumentEncryptionError("Недопустимый путь документа")
        parts = candidate.parts

        if candidate.is_absolute():
            if len(parts) < 3 or parts[-3] != "cases":
                if expected_case_id is not None:
                    raise DocumentEncryptionError(
                        "Путь документа не соответствует хранилищу выбранного дела"
                    )
                return None
            case_part = parts[-2]
            file_part = parts[-1]
        else:
            if len(parts) != 3 or parts[0] != "cases":
                if expected_case_id is not None:
                    raise DocumentEncryptionError(
                        "Путь документа не соответствует хранилищу выбранного дела"
                    )
                return None
            case_part = parts[1]
            file_part = parts[2]

        if (
            not case_part.isdigit()
            or int(case_part) <= 0
            or not _DOCUMENT_CIPHERTEXT_NAME.fullmatch(file_part)
        ):
            if expected_case_id is not None:
                raise DocumentEncryptionError(
                    "Путь документа не соответствует защищённому storage key"
                )
            return None
        case_id = int(case_part)
        if expected_case_id is not None and case_id != int(expected_case_id):
            raise DocumentEncryptionError(
                "Документ относится к другому storage scope дела"
            )
        return Path("cases") / str(case_id) / file_part

    @staticmethod
    def _assert_no_symlink_components(root: Path, relative: Path) -> None:
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise DocumentEncryptionError(
                    "Символьные ссылки для документов запрещены"
                )

    def storage_key_for_case_path(self, path: str | Path, *, case_id: int) -> str:
        """Convert a newly written ciphertext path into the canonical DB key."""

        resolved = Path(path).resolve(strict=False)
        try:
            relative = resolved.relative_to(self.base_dir)
        except ValueError as error:
            raise DocumentEncryptionError(
                "Путь документа находится вне защищённого хранилища"
            ) from error
        key = self._portable_document_key(relative, expected_case_id=case_id)
        if key is None:  # pragma: no cover - expected_case_id makes this fail closed
            raise DocumentEncryptionError("Не удалось сформировать storage key документа")
        self._assert_no_symlink_components(self.base_dir, key)
        return key.as_posix()

    def resolve_storage_path(
        self,
        storage_path: str | Path,
        *,
        expected_case_id: int | None = None,
    ) -> Path:
        raw = str(storage_path or "").strip()
        if not raw:
            raise DocumentEncryptionError("Путь документа не задан")
        candidate = Path(raw)
        if ".." in candidate.parts:
            raise DocumentEncryptionError("Недопустимый путь документа")

        portable_key = self._portable_document_key(
            candidate,
            expected_case_id=expected_case_id,
        )
        if portable_key is not None:
            # This branch is used for current canonical relative keys and for
            # legacy absolute paths. The old absolute prefix is never
            # dereferenced.
            lexical = self.base_dir / portable_key
            relative = portable_key
        else:
            # Backward-compatible utility behavior for non-document files/tests:
            # arbitrary paths are allowed only when they already live beneath
            # the current protected root. Restore rebasing is reserved for the
            # explicit document-key shape above.
            lexical = candidate if candidate.is_absolute() else self.base_dir / candidate
            absolute_lexical = Path(os.path.abspath(os.fspath(lexical)))
            try:
                relative = absolute_lexical.relative_to(self.base_dir)
            except ValueError as error:
                raise DocumentEncryptionError(
                    "Путь документа находится вне защищённого хранилища"
                ) from error
            lexical = absolute_lexical

        self._assert_no_symlink_components(self.base_dir, relative)
        resolved = lexical.resolve(strict=False)
        try:
            resolved.relative_to(self.base_dir)
        except ValueError as error:
            raise DocumentEncryptionError(
                "Путь документа находится вне защищённого хранилища"
            ) from error
        return resolved

    @staticmethod
    def _unlink_and_sync(path: Path) -> bool:
        if not path.exists():
            return False
        if path.is_symlink() or not path.is_file():
            raise DocumentEncryptionError("Небезопасный тип файла при удалении")
        parent = path.parent
        path.unlink()
        try:
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
        return True

    def discard_stored_file(
        self,
        storage_path: str | Path,
        *,
        expected_case_id: int | None = None,
    ) -> bool:
        return self._unlink_and_sync(
            self.resolve_storage_path(
                storage_path,
                expected_case_id=expected_case_id,
            )
        )

    def read_document_bytes(
        self,
        storage_path: str | Path,
        *,
        expected_case_id: int | None = None,
        expected_sha256: str | None = None,
        encryption_key_id: str | None = None,
        encryption_envelope_id: str | None = None,
        encrypted_data_key: str | None = None,
        encrypted_data_key_nonce: str | None = None,
    ) -> bytes:
        resolved = self.resolve_storage_path(
            storage_path,
            expected_case_id=expected_case_id,
        )
        plaintext, _ = decrypt_file_bytes(
            resolved,
            expected_sha256=expected_sha256,
            encryption_key_id=encryption_key_id,
            encryption_envelope_id=encryption_envelope_id,
            encrypted_data_key=encrypted_data_key,
            encrypted_data_key_nonce=encrypted_data_key_nonce,
        )
        return plaintext

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
        target: Path | None = None
        try:
            telegram_file = await bot.get_file(telegram_file_id)
            await bot.download_file(telegram_file.file_path, destination=temporary)

            # Enforce the real byte limit before handing untrusted content to
            # any scanner or parser. Oversized/empty payloads are deleted
            # immediately rather than copied into quarantine.
            try:
                validate_downloaded_size(temporary, max_bytes=self.max_upload_bytes)
            except UploadSecurityError:
                temporary.unlink(missing_ok=True)
                raise

            # DLC-INT-00: bytes stay isolated in .incoming until the malware
            # scanner has returned an admissible verdict. Structural/type checks
            # remain a separate second gate and the hashes must agree.
            malware_scan = await self.malware_scanner.scan(temporary)
            assert_malware_scan_admitted(malware_scan)
            inspection = inspect_upload(
                temporary,
                original_name=preflight.safe_name,
                claimed_mime=mime_type,
                declared_size=file_size,
                max_bytes=self.max_upload_bytes,
            )
            if malware_scan.sha256 != inspection.sha256:
                raise UploadSecurityError(
                    "malware_scan_hash_mismatch",
                    "Файл изменился во время проверки безопасности. Загрузите его повторно.",
                    technical_message="Malware and structural admission hashes differ",
                    sha256=inspection.sha256,
                    security_reason=malware_scan.security_reason,
                )

            case_dir = self.base_dir / "cases" / str(int(case_id))
            case_dir.mkdir(parents=True, exist_ok=True)
            try:
                case_dir.chmod(0o700)
            except OSError:
                pass

            # A random storage key prevents two database rows from sharing one
            # ciphertext container. Duplicate business content is rejected by the
            # database service and the newly created file is then discarded.
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
                storage_path=self.storage_key_for_case_path(target, case_id=case_id),
                mime_type=inspection.mime_type,
                file_size=inspection.size_bytes,
                sha256=inspection.sha256,
                detected_type=inspection.detected_type,
                security_status="VERIFIED",
                security_reason=malware_scan.security_reason,
                scanned_at=max(inspection.scanned_at, malware_scan.scanned_at),
                encryption_status=ENCRYPTION_STATUS,
                encryption_key_id=encryption.key_id,
                encryption_format_version=encryption.format_version,
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
                            quarantine_dir=self.base_dir / "quarantine",
                            error=error,
                            safe_name=preflight.safe_name,
                            case_id=case_id,
                        )
                    except Exception:
                        # A failed quarantine operation must never leave rejected
                        # plaintext in .incoming.
                        temporary.unlink(missing_ok=True)
                else:
                    temporary.unlink(missing_ok=True)
            raise
        except Exception:
            temporary.unlink(missing_ok=True)
            if target is not None and target.exists():
                try:
                    self._unlink_and_sync(target)
                except OSError:
                    target.unlink(missing_ok=True)
            raise


__all__ = ["LocalStorageService", "StoredFile", "safe_filename"]
