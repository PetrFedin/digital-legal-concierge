from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings
from app.security.keyring import KeyEntry, document_encryption_ring

MAGIC_V1 = b"DLCENC1\x00"
MAGIC_V2 = b"DLCENC2\x00"
MAGIC = MAGIC_V2
FORMAT_V1 = 1
FORMAT_V2 = 2
NONCE_SIZE = 12
DEK_SIZE = 32
ENVELOPE_ID_SIZE = 16
DIGEST_SIZE = 32
TAG_SIZE = 16
MAX_KEY_ID_BYTES = 32
ENCRYPTION_STATUS = "ENCRYPTED"
LEGACY_STATUS = "LEGACY_PLAINTEXT"


class DocumentEncryptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncryptionMetadata:
    key_id: str | None
    sha256: str
    plaintext_size: int
    encrypted_at: datetime
    format_version: int = FORMAT_V1
    envelope_id: str | None = None
    encrypted_data_key: str | None = None
    encrypted_data_key_nonce: str | None = None

    @property
    def has_usable_envelope(self) -> bool:
        return bool(
            self.format_version == FORMAT_V2
            and self.key_id
            and self.envelope_id
            and self.encrypted_data_key
            and self.encrypted_data_key_nonce
        )


def _derive_v1_key(entry: KeyEntry) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"digital-legal-concierge/document-encryption/v1",
        info=f"document-encryption:{entry.key_id}".encode("ascii"),
    ).derive(entry.secret.encode("utf-8"))


def _derive_kek(entry: KeyEntry) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"digital-legal-concierge/document-envelope/v2",
        info=f"document-kek:{entry.key_id}".encode("ascii"),
    ).derive(entry.secret.encode("utf-8"))


def _maximum_plaintext_bytes() -> int:
    return max(1, int(settings.max_document_upload_mb)) * 1024 * 1024


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: str | None, *, field: str) -> bytes:
    if not value:
        raise DocumentEncryptionError(f"Отсутствует {field} документа")
    try:
        decoded = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError, binascii.Error) as error:
        raise DocumentEncryptionError(f"Повреждён {field} документа") from error
    return decoded


def _validate_key_id(key_id: str | None) -> str:
    value = str(key_id or "")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise DocumentEncryptionError("Некорректный идентификатор ключа документа") from error
    if not 1 <= len(encoded) <= MAX_KEY_ID_BYTES:
        raise DocumentEncryptionError("Некорректный идентификатор ключа документа")
    return value


def _envelope_id_bytes(envelope_id: str | None) -> bytes:
    try:
        value = bytes.fromhex(str(envelope_id or ""))
    except ValueError as error:
        raise DocumentEncryptionError("Некорректный envelope id документа") from error
    if len(value) != ENVELOPE_ID_SIZE:
        raise DocumentEncryptionError("Некорректный envelope id документа")
    return value


def _envelope_aad(*, envelope_id: bytes, key_id: str) -> bytes:
    key_bytes = _validate_key_id(key_id).encode("ascii")
    return b"DLC-DEK-V2\x00" + envelope_id + bytes([len(key_bytes)]) + key_bytes


def _write_atomic(target_path: Path, header: bytes, ciphertext: bytes) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=target_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as stream:
            stream.write(header)
            stream.write(ciphertext)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, target_path)
        try:
            directory_descriptor = os.open(target_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            pass
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _sha256(plaintext: bytes, expected_sha256: str | None) -> str:
    if len(plaintext) > _maximum_plaintext_bytes():
        raise DocumentEncryptionError("Документ превышает допустимый размер")
    actual = hashlib.sha256(plaintext).hexdigest()
    if expected_sha256 and not hmac.compare_digest(actual, str(expected_sha256).lower()):
        raise DocumentEncryptionError("Контрольная сумма документа изменилась")
    return actual


def is_encrypted_file(path: str | Path) -> bool:
    candidate = Path(path)
    if not candidate.is_file() or candidate.is_symlink():
        return False
    try:
        with candidate.open("rb") as stream:
            magic = stream.read(len(MAGIC_V2))
            return magic in {MAGIC_V1, MAGIC_V2}
    except OSError:
        return False


def encrypted_format_version(path: str | Path) -> int | None:
    candidate = Path(path)
    if not candidate.is_file() or candidate.is_symlink():
        return None
    try:
        with candidate.open("rb") as stream:
            magic = stream.read(len(MAGIC_V2))
    except OSError:
        return None
    if magic == MAGIC_V2:
        return FORMAT_V2
    if magic == MAGIC_V1:
        return FORMAT_V1
    return None


def _build_v1_header(*, key_id: str, sha256_hex: str, nonce: bytes) -> bytes:
    key_bytes = _validate_key_id(key_id).encode("ascii")
    try:
        digest = bytes.fromhex(sha256_hex)
    except ValueError as error:
        raise DocumentEncryptionError("Некорректный SHA-256 документа") from error
    if len(digest) != DIGEST_SIZE or len(nonce) != NONCE_SIZE:
        raise DocumentEncryptionError("Некорректные параметры шифрования документа")
    return MAGIC_V1 + bytes([len(key_bytes)]) + key_bytes + digest + nonce


def _parse_v1(payload: bytes) -> tuple[str, str, bytes, bytes, bytes]:
    minimum = len(MAGIC_V1) + 1 + 1 + DIGEST_SIZE + NONCE_SIZE + TAG_SIZE
    if len(payload) < minimum or payload[: len(MAGIC_V1)] != MAGIC_V1:
        raise DocumentEncryptionError("Файл не имеет поддерживаемого формата шифрования")
    key_length = payload[len(MAGIC_V1)]
    if not 1 <= key_length <= MAX_KEY_ID_BYTES:
        raise DocumentEncryptionError("Повреждён заголовок зашифрованного документа")
    header_size = len(MAGIC_V1) + 1 + key_length + DIGEST_SIZE + NONCE_SIZE
    if len(payload) < header_size + TAG_SIZE:
        raise DocumentEncryptionError("Зашифрованный документ усечён")
    offset = len(MAGIC_V1) + 1
    try:
        key_id = payload[offset : offset + key_length].decode("ascii")
    except UnicodeDecodeError as error:
        raise DocumentEncryptionError("Повреждён идентификатор ключа документа") from error
    offset += key_length
    digest = payload[offset : offset + DIGEST_SIZE]
    offset += DIGEST_SIZE
    nonce = payload[offset : offset + NONCE_SIZE]
    header = payload[:header_size]
    ciphertext = payload[header_size:]
    return key_id, digest.hex(), nonce, header, ciphertext


def _build_v2_header(*, envelope_id: bytes, sha256_hex: str, nonce: bytes) -> bytes:
    try:
        digest = bytes.fromhex(sha256_hex)
    except ValueError as error:
        raise DocumentEncryptionError("Некорректный SHA-256 документа") from error
    if (
        len(envelope_id) != ENVELOPE_ID_SIZE
        or len(digest) != DIGEST_SIZE
        or len(nonce) != NONCE_SIZE
    ):
        raise DocumentEncryptionError("Некорректные параметры envelope-шифрования")
    return MAGIC_V2 + envelope_id + digest + nonce


def _parse_v2(payload: bytes) -> tuple[str, str, bytes, bytes, bytes]:
    header_size = len(MAGIC_V2) + ENVELOPE_ID_SIZE + DIGEST_SIZE + NONCE_SIZE
    if len(payload) < header_size + TAG_SIZE or payload[: len(MAGIC_V2)] != MAGIC_V2:
        raise DocumentEncryptionError("Зашифрованный документ DLCENC2 усечён")
    offset = len(MAGIC_V2)
    envelope_id = payload[offset : offset + ENVELOPE_ID_SIZE]
    offset += ENVELOPE_ID_SIZE
    digest = payload[offset : offset + DIGEST_SIZE]
    offset += DIGEST_SIZE
    nonce = payload[offset : offset + NONCE_SIZE]
    return envelope_id.hex(), digest.hex(), nonce, payload[:header_size], payload[header_size:]


def _write_v1_bytes(
    plaintext: bytes,
    target_path: Path,
    *,
    expected_sha256: str | None = None,
) -> EncryptionMetadata:
    sha256_hex = _sha256(plaintext, expected_sha256)
    entry = document_encryption_ring().require_active()
    nonce = os.urandom(NONCE_SIZE)
    header = _build_v1_header(key_id=entry.key_id, sha256_hex=sha256_hex, nonce=nonce)
    ciphertext = AESGCM(_derive_v1_key(entry)).encrypt(nonce, plaintext, header)
    _write_atomic(target_path, header, ciphertext)
    return EncryptionMetadata(
        key_id=entry.key_id,
        sha256=sha256_hex,
        plaintext_size=len(plaintext),
        encrypted_at=datetime.now(timezone.utc),
        format_version=FORMAT_V1,
    )


def _write_v2_bytes(
    plaintext: bytes,
    target_path: Path,
    *,
    expected_sha256: str | None = None,
) -> EncryptionMetadata:
    sha256_hex = _sha256(plaintext, expected_sha256)
    entry = document_encryption_ring().require_active()
    envelope_id = os.urandom(ENVELOPE_ID_SIZE)
    data_nonce = os.urandom(NONCE_SIZE)
    data_key = os.urandom(DEK_SIZE)
    header = _build_v2_header(
        envelope_id=envelope_id,
        sha256_hex=sha256_hex,
        nonce=data_nonce,
    )
    ciphertext = AESGCM(data_key).encrypt(data_nonce, plaintext, header)

    wrap_nonce = os.urandom(NONCE_SIZE)
    wrapped_key = AESGCM(_derive_kek(entry)).encrypt(
        wrap_nonce,
        data_key,
        _envelope_aad(envelope_id=envelope_id, key_id=entry.key_id),
    )
    _write_atomic(target_path, header, ciphertext)
    return EncryptionMetadata(
        key_id=entry.key_id,
        sha256=sha256_hex,
        plaintext_size=len(plaintext),
        encrypted_at=datetime.now(timezone.utc),
        format_version=FORMAT_V2,
        envelope_id=envelope_id.hex(),
        encrypted_data_key=_encode(wrapped_key),
        encrypted_data_key_nonce=_encode(wrap_nonce),
    )


def encrypted_file_metadata(path: str | Path) -> EncryptionMetadata:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise DocumentEncryptionError("Зашифрованный документ не найден")
    payload = candidate.read_bytes()
    modified = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
    if payload.startswith(MAGIC_V1):
        key_id, sha256_hex, _, _, ciphertext = _parse_v1(payload)
        return EncryptionMetadata(
            key_id=key_id,
            sha256=sha256_hex,
            plaintext_size=max(0, len(ciphertext) - TAG_SIZE),
            encrypted_at=modified,
            format_version=FORMAT_V1,
        )
    if payload.startswith(MAGIC_V2):
        envelope_id, sha256_hex, _, _, ciphertext = _parse_v2(payload)
        return EncryptionMetadata(
            key_id=None,
            sha256=sha256_hex,
            plaintext_size=max(0, len(ciphertext) - TAG_SIZE),
            encrypted_at=modified,
            format_version=FORMAT_V2,
            envelope_id=envelope_id,
        )
    raise DocumentEncryptionError("Файл не имеет поддерживаемого формата шифрования")


def encrypt_file(
    source: str | Path,
    target: str | Path,
    *,
    expected_sha256: str | None = None,
) -> EncryptionMetadata:
    source_path = Path(source)
    if source_path.is_symlink() or not source_path.is_file():
        raise DocumentEncryptionError("Исходный документ не найден")
    return _write_v2_bytes(
        source_path.read_bytes(),
        Path(target),
        expected_sha256=expected_sha256,
    )


def encrypt_file_legacy_v1(
    source: str | Path,
    target: str | Path,
    *,
    expected_sha256: str | None = None,
) -> EncryptionMetadata:
    """Test/migration helper; new application writes must use ``encrypt_file``."""

    source_path = Path(source)
    if source_path.is_symlink() or not source_path.is_file():
        raise DocumentEncryptionError("Исходный документ не найден")
    return _write_v1_bytes(
        source_path.read_bytes(),
        Path(target),
        expected_sha256=expected_sha256,
    )


def _verify_plaintext(
    plaintext: bytes,
    *,
    header_sha256: str,
    expected_sha256: str | None,
) -> str:
    actual_sha256 = hashlib.sha256(plaintext).hexdigest()
    if not hmac.compare_digest(actual_sha256, header_sha256):
        raise DocumentEncryptionError("Контрольная сумма документа не совпадает")
    if expected_sha256 and not hmac.compare_digest(
        actual_sha256,
        str(expected_sha256).lower(),
    ):
        raise DocumentEncryptionError("Документ не соответствует записи в базе")
    return actual_sha256


def decrypt_file_bytes(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    encryption_key_id: str | None = None,
    encryption_envelope_id: str | None = None,
    encrypted_data_key: str | None = None,
    encrypted_data_key_nonce: str | None = None,
) -> tuple[bytes, EncryptionMetadata]:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise DocumentEncryptionError("Зашифрованный документ не найден")
    payload = candidate.read_bytes()
    maximum_ciphertext = _maximum_plaintext_bytes() + 1024
    if len(payload) > maximum_ciphertext:
        raise DocumentEncryptionError("Зашифрованный документ превышает допустимый размер")
    modified = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)

    if payload.startswith(MAGIC_V1):
        key_id, sha256_hex, nonce, header, ciphertext = _parse_v1(payload)
        entry = document_encryption_ring().by_id(key_id)
        if entry is None:
            raise DocumentEncryptionError(f"Ключ расшифрования документа {key_id} отсутствует")
        try:
            plaintext = AESGCM(_derive_v1_key(entry)).decrypt(nonce, ciphertext, header)
        except InvalidTag as error:
            raise DocumentEncryptionError("Целостность зашифрованного документа нарушена") from error
        actual_sha256 = _verify_plaintext(
            plaintext,
            header_sha256=sha256_hex,
            expected_sha256=expected_sha256,
        )
        return plaintext, EncryptionMetadata(
            key_id=key_id,
            sha256=actual_sha256,
            plaintext_size=len(plaintext),
            encrypted_at=modified,
            format_version=FORMAT_V1,
        )

    if not payload.startswith(MAGIC_V2):
        raise DocumentEncryptionError("Файл не имеет поддерживаемого формата шифрования")

    envelope_id, sha256_hex, nonce, header, ciphertext = _parse_v2(payload)
    if not encryption_envelope_id or not hmac.compare_digest(
        envelope_id,
        str(encryption_envelope_id).lower(),
    ):
        raise DocumentEncryptionError("Envelope документа не соответствует файлу")
    key_id = _validate_key_id(encryption_key_id)
    entry = document_encryption_ring().by_id(key_id)
    if entry is None:
        raise DocumentEncryptionError(f"Ключ расшифрования документа {key_id} отсутствует")
    wrap_nonce = _decode(encrypted_data_key_nonce, field="nonce обёрнутого ключа")
    wrapped_key = _decode(encrypted_data_key, field="обёрнутый ключ")
    if len(wrap_nonce) != NONCE_SIZE:
        raise DocumentEncryptionError("Повреждён nonce обёрнутого ключа документа")
    try:
        data_key = AESGCM(_derive_kek(entry)).decrypt(
            wrap_nonce,
            wrapped_key,
            _envelope_aad(
                envelope_id=_envelope_id_bytes(envelope_id),
                key_id=key_id,
            ),
        )
    except InvalidTag as error:
        raise DocumentEncryptionError("Обёрнутый ключ документа повреждён") from error
    if len(data_key) != DEK_SIZE:
        raise DocumentEncryptionError("Некорректная длина ключа документа")
    try:
        plaintext = AESGCM(data_key).decrypt(nonce, ciphertext, header)
    except InvalidTag as error:
        raise DocumentEncryptionError("Целостность зашифрованного документа нарушена") from error
    actual_sha256 = _verify_plaintext(
        plaintext,
        header_sha256=sha256_hex,
        expected_sha256=expected_sha256,
    )
    return plaintext, EncryptionMetadata(
        key_id=key_id,
        sha256=actual_sha256,
        plaintext_size=len(plaintext),
        encrypted_at=modified,
        format_version=FORMAT_V2,
        envelope_id=envelope_id,
        encrypted_data_key=encrypted_data_key,
        encrypted_data_key_nonce=encrypted_data_key_nonce,
    )


def rewrap_encryption_metadata(metadata: EncryptionMetadata) -> EncryptionMetadata:
    if not metadata.has_usable_envelope:
        raise DocumentEncryptionError("Envelope-метаданные документа неполны")
    old_key_id = _validate_key_id(metadata.key_id)
    old_entry = document_encryption_ring().by_id(old_key_id)
    if old_entry is None:
        raise DocumentEncryptionError(f"Ключ расшифрования документа {old_key_id} отсутствует")
    active = document_encryption_ring().require_active()
    if old_key_id == active.key_id:
        return metadata

    envelope_bytes = _envelope_id_bytes(metadata.envelope_id)
    old_nonce = _decode(metadata.encrypted_data_key_nonce, field="nonce обёрнутого ключа")
    wrapped_key = _decode(metadata.encrypted_data_key, field="обёрнутый ключ")
    try:
        data_key = AESGCM(_derive_kek(old_entry)).decrypt(
            old_nonce,
            wrapped_key,
            _envelope_aad(envelope_id=envelope_bytes, key_id=old_key_id),
        )
    except InvalidTag as error:
        raise DocumentEncryptionError("Обёрнутый ключ документа повреждён") from error

    new_nonce = os.urandom(NONCE_SIZE)
    new_wrapped_key = AESGCM(_derive_kek(active)).encrypt(
        new_nonce,
        data_key,
        _envelope_aad(envelope_id=envelope_bytes, key_id=active.key_id),
    )
    return replace(
        metadata,
        key_id=active.key_id,
        encrypted_data_key=_encode(new_wrapped_key),
        encrypted_data_key_nonce=_encode(new_nonce),
        encrypted_at=datetime.now(timezone.utc),
    )


def rotate_encrypted_file(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    encryption_key_id: str | None = None,
    encryption_envelope_id: str | None = None,
    encrypted_data_key: str | None = None,
    encrypted_data_key_nonce: str | None = None,
) -> EncryptionMetadata:
    candidate = Path(path)
    version = encrypted_format_version(candidate)
    if version == FORMAT_V1:
        plaintext, current = decrypt_file_bytes(
            candidate,
            expected_sha256=expected_sha256,
        )
        return _write_v2_bytes(
            plaintext,
            candidate,
            expected_sha256=current.sha256,
        )
    if version != FORMAT_V2:
        raise DocumentEncryptionError("Файл не имеет поддерживаемого формата шифрования")

    _, current = decrypt_file_bytes(
        candidate,
        expected_sha256=expected_sha256,
        encryption_key_id=encryption_key_id,
        encryption_envelope_id=encryption_envelope_id,
        encrypted_data_key=encrypted_data_key,
        encrypted_data_key_nonce=encrypted_data_key_nonce,
    )
    return rewrap_encryption_metadata(current)
