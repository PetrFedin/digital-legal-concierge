from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings
from app.security.keyring import KeyEntry, document_encryption_ring

MAGIC = b"DLCENC1\x00"
NONCE_SIZE = 12
DIGEST_SIZE = 32
TAG_SIZE = 16
MAX_KEY_ID_BYTES = 32
ENCRYPTION_STATUS = "ENCRYPTED"
LEGACY_STATUS = "LEGACY_PLAINTEXT"


class DocumentEncryptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncryptionMetadata:
    key_id: str
    sha256: str
    plaintext_size: int
    encrypted_at: datetime


def _derive_key(entry: KeyEntry) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"digital-legal-concierge/document-encryption/v1",
        info=f"document-encryption:{entry.key_id}".encode("ascii"),
    ).derive(entry.secret.encode("utf-8"))


def _maximum_plaintext_bytes() -> int:
    return max(1, int(settings.max_document_upload_mb)) * 1024 * 1024


def is_encrypted_file(path: str | Path) -> bool:
    candidate = Path(path)
    if not candidate.is_file() or candidate.is_symlink():
        return False
    try:
        with candidate.open("rb") as stream:
            return stream.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _build_header(*, key_id: str, sha256_hex: str, nonce: bytes) -> bytes:
    key_bytes = key_id.encode("ascii")
    if not 1 <= len(key_bytes) <= MAX_KEY_ID_BYTES:
        raise DocumentEncryptionError("Некорректный идентификатор ключа документа")
    try:
        digest = bytes.fromhex(sha256_hex)
    except ValueError as error:
        raise DocumentEncryptionError("Некорректный SHA-256 документа") from error
    if len(digest) != DIGEST_SIZE or len(nonce) != NONCE_SIZE:
        raise DocumentEncryptionError("Некорректные параметры шифрования документа")
    return MAGIC + bytes([len(key_bytes)]) + key_bytes + digest + nonce


def _parse_payload(payload: bytes) -> tuple[str, str, bytes, bytes, bytes]:
    minimum = len(MAGIC) + 1 + 1 + DIGEST_SIZE + NONCE_SIZE + TAG_SIZE
    if len(payload) < minimum or payload[: len(MAGIC)] != MAGIC:
        raise DocumentEncryptionError("Файл не имеет поддерживаемого формата шифрования")
    key_length = payload[len(MAGIC)]
    if not 1 <= key_length <= MAX_KEY_ID_BYTES:
        raise DocumentEncryptionError("Повреждён заголовок зашифрованного документа")
    header_size = len(MAGIC) + 1 + key_length + DIGEST_SIZE + NONCE_SIZE
    if len(payload) < header_size + TAG_SIZE:
        raise DocumentEncryptionError("Зашифрованный документ усечён")
    offset = len(MAGIC) + 1
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


def _write_encrypted_bytes(
    plaintext: bytes,
    target_path: Path,
    *,
    expected_sha256: str | None = None,
) -> EncryptionMetadata:
    if len(plaintext) > _maximum_plaintext_bytes():
        raise DocumentEncryptionError("Документ превышает допустимый размер")
    sha256_hex = hashlib.sha256(plaintext).hexdigest()
    if expected_sha256 and not hmac.compare_digest(
        sha256_hex,
        str(expected_sha256).lower(),
    ):
        raise DocumentEncryptionError("Контрольная сумма документа изменилась")

    entry = document_encryption_ring().require_active()
    nonce = os.urandom(NONCE_SIZE)
    header = _build_header(key_id=entry.key_id, sha256_hex=sha256_hex, nonce=nonce)
    ciphertext = AESGCM(_derive_key(entry)).encrypt(nonce, plaintext, header)

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

    return EncryptionMetadata(
        key_id=entry.key_id,
        sha256=sha256_hex,
        plaintext_size=len(plaintext),
        encrypted_at=datetime.now(timezone.utc),
    )


def encrypted_file_metadata(path: str | Path) -> EncryptionMetadata:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise DocumentEncryptionError("Зашифрованный документ не найден")
    payload = candidate.read_bytes()
    key_id, sha256_hex, _, _, ciphertext = _parse_payload(payload)
    return EncryptionMetadata(
        key_id=key_id,
        sha256=sha256_hex,
        plaintext_size=max(0, len(ciphertext) - TAG_SIZE),
        encrypted_at=datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc),
    )


def encrypt_file(
    source: str | Path,
    target: str | Path,
    *,
    expected_sha256: str | None = None,
) -> EncryptionMetadata:
    source_path = Path(source)
    if source_path.is_symlink() or not source_path.is_file():
        raise DocumentEncryptionError("Исходный документ не найден")
    return _write_encrypted_bytes(
        source_path.read_bytes(),
        Path(target),
        expected_sha256=expected_sha256,
    )


def decrypt_file_bytes(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
) -> tuple[bytes, EncryptionMetadata]:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise DocumentEncryptionError("Зашифрованный документ не найден")
    payload = candidate.read_bytes()
    maximum_ciphertext = _maximum_plaintext_bytes() + 512
    if len(payload) > maximum_ciphertext:
        raise DocumentEncryptionError("Зашифрованный документ превышает допустимый размер")
    key_id, sha256_hex, nonce, header, ciphertext = _parse_payload(payload)
    entry = document_encryption_ring().by_id(key_id)
    if entry is None:
        raise DocumentEncryptionError(
            f"Ключ расшифрования документа {key_id} отсутствует"
        )
    try:
        plaintext = AESGCM(_derive_key(entry)).decrypt(nonce, ciphertext, header)
    except InvalidTag as error:
        raise DocumentEncryptionError(
            "Целостность зашифрованного документа нарушена"
        ) from error
    actual_sha256 = hashlib.sha256(plaintext).hexdigest()
    if not hmac.compare_digest(actual_sha256, sha256_hex):
        raise DocumentEncryptionError("Контрольная сумма документа не совпадает")
    if expected_sha256 and not hmac.compare_digest(
        actual_sha256,
        str(expected_sha256).lower(),
    ):
        raise DocumentEncryptionError("Документ не соответствует записи в базе")
    return plaintext, EncryptionMetadata(
        key_id=key_id,
        sha256=actual_sha256,
        plaintext_size=len(plaintext),
        encrypted_at=datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc),
    )


def rotate_encrypted_file(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
) -> EncryptionMetadata:
    candidate = Path(path)
    plaintext, current = decrypt_file_bytes(
        candidate,
        expected_sha256=expected_sha256,
    )
    active = document_encryption_ring().require_active()
    if current.key_id == active.key_id:
        return current
    return _write_encrypted_bytes(
        plaintext,
        candidate,
        expected_sha256=current.sha256,
    )
