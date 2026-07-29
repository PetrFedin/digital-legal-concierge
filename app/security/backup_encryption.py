from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import struct
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.engine import make_url

from app.config import settings
from app.security.keyring import KeyEntry, backup_encryption_ring

MAGIC = b"DLCBKP1\x00"
FORMAT_VERSION = 1
HEADER_LENGTH_SIZE = 4
NONCE_SIZE = 12
TAG_SIZE = 16
MAX_HEADER_BYTES = 64 * 1024
CHUNK_SIZE = 1024 * 1024
BACKUP_SUFFIX = ".dlcbak"
EXCLUDED_STORAGE_DIRS = {".incoming", "quarantine"}


class BackupSecurityError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupMetadata:
    format_version: int
    key_id: str
    created_at: str
    plaintext_sha256: str
    plaintext_size: int
    content_type: str
    manifest_version: int
    verified: bool = False


@dataclass(frozen=True)
class BackupResult:
    path: str
    key_id: str
    created_at: str
    plaintext_sha256: str
    plaintext_size: int
    encrypted_size: int
    verified: bool
    files_count: int


def _derive_key(entry: KeyEntry) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"digital-legal-concierge/backup-encryption/v1",
        info=f"backup-encryption:{entry.key_id}".encode("ascii"),
    ).derive(entry.secret.encode("utf-8"))


def _backup_limit_bytes() -> int:
    return max(1, int(settings.max_backup_mb)) * 1024 * 1024


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
            if size > _backup_limit_bytes():
                raise BackupSecurityError("Резервная копия превышает допустимый размер")
    return digest.hexdigest(), size


def _header_bytes(metadata: dict) -> bytes:
    raw = json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if not 1 <= len(raw) <= MAX_HEADER_BYTES:
        raise BackupSecurityError("Некорректный размер заголовка резервной копии")
    return MAGIC + struct.pack(">I", len(raw)) + raw


def _read_header(stream: BinaryIO) -> tuple[BackupMetadata, bytes, bytes]:
    magic = stream.read(len(MAGIC))
    if magic != MAGIC:
        raise BackupSecurityError("Файл не является зашифрованной резервной копией DLC")
    raw_length = stream.read(HEADER_LENGTH_SIZE)
    if len(raw_length) != HEADER_LENGTH_SIZE:
        raise BackupSecurityError("Заголовок резервной копии усечён")
    header_length = struct.unpack(">I", raw_length)[0]
    if not 1 <= header_length <= MAX_HEADER_BYTES:
        raise BackupSecurityError("Повреждён размер заголовка резервной копии")
    raw_header = stream.read(header_length)
    if len(raw_header) != header_length:
        raise BackupSecurityError("Заголовок резервной копии усечён")
    try:
        payload = json.loads(raw_header.decode("utf-8"))
        nonce = base64.urlsafe_b64decode(str(payload["nonce"]) + "==")
        metadata = BackupMetadata(
            format_version=int(payload["format_version"]),
            key_id=str(payload["key_id"]),
            created_at=str(payload["created_at"]),
            plaintext_sha256=str(payload["plaintext_sha256"]),
            plaintext_size=int(payload["plaintext_size"]),
            content_type=str(payload["content_type"]),
            manifest_version=int(payload["manifest_version"]),
            verified=False,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise BackupSecurityError("Повреждён заголовок резервной копии") from error
    if metadata.format_version != FORMAT_VERSION:
        raise BackupSecurityError("Версия формата резервной копии не поддерживается")
    if metadata.content_type != "application/vnd.dlc.backup+tar.gz":
        raise BackupSecurityError("Тип содержимого резервной копии не поддерживается")
    if len(nonce) != NONCE_SIZE:
        raise BackupSecurityError("Повреждён nonce резервной копии")
    if len(metadata.plaintext_sha256) != 64 or metadata.plaintext_size < 0:
        raise BackupSecurityError("Повреждена контрольная сумма резервной копии")
    prefix = MAGIC + raw_length + raw_header
    return metadata, nonce, prefix


def inspect_encrypted_backup(path: str | Path) -> BackupMetadata:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise BackupSecurityError("Резервная копия не найдена")
    with candidate.open("rb") as stream:
        metadata, _, _ = _read_header(stream)
    return metadata


def encrypt_backup_payload(source: str | Path, target: str | Path) -> BackupMetadata:
    source_path = Path(source)
    target_path = Path(target)
    if source_path.is_symlink() or not source_path.is_file():
        raise BackupSecurityError("Исходный backup payload не найден")
    plaintext_sha256, plaintext_size = _sha256_file(source_path)
    entry = backup_encryption_ring().require_active()
    nonce = os.urandom(NONCE_SIZE)
    created_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "format_version": FORMAT_VERSION,
        "key_id": entry.key_id,
        "created_at": created_at,
        "plaintext_sha256": plaintext_sha256,
        "plaintext_size": plaintext_size,
        "content_type": "application/vnd.dlc.backup+tar.gz",
        "manifest_version": 1,
        "nonce": base64.urlsafe_b64encode(nonce).decode("ascii").rstrip("="),
    }
    prefix = _header_bytes(payload)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_path.parent.chmod(0o700)
    except OSError:
        pass
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=target_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        encryptor = Cipher(
            algorithms.AES(_derive_key(entry)),
            modes.GCM(nonce),
        ).encryptor()
        encryptor.authenticate_additional_data(prefix)
        with source_path.open("rb") as source_stream, os.fdopen(descriptor, "wb") as out:
            out.write(prefix)
            while chunk := source_stream.read(CHUNK_SIZE):
                out.write(encryptor.update(chunk))
            out.write(encryptor.finalize())
            out.write(encryptor.tag)
            out.flush()
            os.fsync(out.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, target_path)
        try:
            directory_descriptor = os.open(target_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            pass
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return BackupMetadata(
        format_version=FORMAT_VERSION,
        key_id=entry.key_id,
        created_at=created_at,
        plaintext_sha256=plaintext_sha256,
        plaintext_size=plaintext_size,
        content_type="application/vnd.dlc.backup+tar.gz",
        manifest_version=1,
        verified=False,
    )


def _decrypt_backup_stream(
    source: Path,
    output: BinaryIO | None = None,
) -> BackupMetadata:
    if source.is_symlink() or not source.is_file():
        raise BackupSecurityError("Резервная копия не найдена")
    file_size = source.stat().st_size
    if file_size > _backup_limit_bytes() + MAX_HEADER_BYTES + TAG_SIZE:
        raise BackupSecurityError("Зашифрованная резервная копия превышает лимит")
    with source.open("rb") as stream:
        metadata, nonce, prefix = _read_header(stream)
        ciphertext_start = stream.tell()
        if file_size < ciphertext_start + TAG_SIZE:
            raise BackupSecurityError("Зашифрованная резервная копия усечена")
        ciphertext_size = file_size - ciphertext_start - TAG_SIZE
        stream.seek(file_size - TAG_SIZE)
        tag = stream.read(TAG_SIZE)
        stream.seek(ciphertext_start)
        entry = backup_encryption_ring().by_id(metadata.key_id)
        if entry is None:
            raise BackupSecurityError(
                f"Ключ расшифрования резервной копии {metadata.key_id} отсутствует"
            )
        decryptor = Cipher(
            algorithms.AES(_derive_key(entry)),
            modes.GCM(nonce, tag),
        ).decryptor()
        decryptor.authenticate_additional_data(prefix)
        digest = hashlib.sha256()
        plaintext_size = 0
        remaining = ciphertext_size
        try:
            while remaining:
                chunk = stream.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise BackupSecurityError("Зашифрованная резервная копия усечена")
                remaining -= len(chunk)
                plaintext = decryptor.update(chunk)
                digest.update(plaintext)
                plaintext_size += len(plaintext)
                if output is not None:
                    output.write(plaintext)
            final = decryptor.finalize()
        except InvalidTag as error:
            raise BackupSecurityError(
                "Целостность зашифрованной резервной копии нарушена"
            ) from error
        digest.update(final)
        plaintext_size += len(final)
        if output is not None:
            output.write(final)
    actual_sha256 = digest.hexdigest()
    if plaintext_size != metadata.plaintext_size:
        raise BackupSecurityError("Размер расшифрованной резервной копии не совпадает")
    if not hmac.compare_digest(actual_sha256, metadata.plaintext_sha256):
        raise BackupSecurityError("Контрольная сумма резервной копии не совпадает")
    return BackupMetadata(**{**asdict(metadata), "verified": True})


def decrypt_backup_to_file(source: str | Path, target: str | Path) -> BackupMetadata:
    source_path = Path(source)
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=target_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            metadata = _decrypt_backup_stream(source_path, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, target_path)
        return metadata
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _sqlite_database_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise BackupSecurityError(
            "Автоматический backup сейчас поддерживает SQLite; для другой СУБД "
            "нужен штатный provider dump перед шифрованием"
        )
    database = str(url.database or "")
    if not database or database == ":memory:":
        raise BackupSecurityError("In-memory SQLite нельзя резервировать")
    path = Path(database)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if path.is_symlink() or not path.is_file():
        raise BackupSecurityError("Файл базы данных не найден")
    return path


def _snapshot_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(str(source))
    target_connection = sqlite3.connect(str(target))
    try:
        source_connection.execute("PRAGMA query_only=ON")
        source_connection.backup(target_connection)
        target_connection.execute("PRAGMA integrity_check")
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    target.chmod(0o600)


def _copy_storage(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return
    if source.is_symlink() or not source.is_dir():
        raise BackupSecurityError("Каталог документов имеет небезопасный тип")
    for root, directories, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        safe_directories: list[str] = []
        for directory in directories:
            candidate = root_path / directory
            relative = candidate.relative_to(source)
            if relative.parts and relative.parts[0] in EXCLUDED_STORAGE_DIRS:
                continue
            if candidate.is_symlink():
                raise BackupSecurityError("Символьные ссылки в хранилище запрещены")
            safe_directories.append(directory)
        directories[:] = safe_directories
        relative_root = root_path.relative_to(source)
        destination_root = target / relative_root
        destination_root.mkdir(parents=True, exist_ok=True)
        for filename in files:
            candidate = root_path / filename
            if candidate.is_symlink() or not candidate.is_file():
                raise BackupSecurityError("Небезопасный файл в хранилище документов")
            destination = destination_root / filename
            shutil.copy2(candidate, destination)
            destination.chmod(0o600)


def _manifest_entries(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise BackupSecurityError("Символьные ссылки в backup payload запрещены")
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        digest, size = _sha256_file(path)
        entries.append({"path": relative, "sha256": digest, "size": size})
    return entries


def _write_manifest(root: Path, *, database_name: str) -> dict:
    restore_dir = root / "restore"
    restore_dir.mkdir(parents=True, exist_ok=True)
    readme = restore_dir / "README.txt"
    readme.write_text(
        "Проверенная staging-копия. Не заменяйте рабочие данные при запущенном сервисе.\n"
        "Сначала остановите приложение, сохраните текущие данные отдельно, затем "
        "перенесите database/ и storage/ по утверждённой процедуре восстановления.\n"
        "Секреты окружения намеренно не входят в архив и восстанавливаются из "
        "внешнего secret manager.\n",
        encoding="utf-8",
    )
    readme.chmod(0o600)
    manifest = {
        "manifest_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": {"engine": "sqlite", "file": f"database/{database_name}"},
        "storage": {
            "root": "storage",
            "excluded_directories": sorted(EXCLUDED_STORAGE_DIRS),
        },
        "secrets_included": False,
        "files": _manifest_entries(root),
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    return manifest


def _build_tar_payload(root: Path, target: Path) -> None:
    with tarfile.open(target, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            archive.add(child, arcname=child.name, recursive=True)
    target.chmod(0o600)


def _safe_member_path(root: Path, name: str) -> Path:
    pure = PurePosixPath(name)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise BackupSecurityError("Архив содержит небезопасный путь")
    target = (root / Path(*pure.parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as error:
        raise BackupSecurityError("Архив пытается выйти за каталог восстановления") from error
    return target


def _extract_tar_safely(payload: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(payload, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise BackupSecurityError("Архив содержит запрещённый тип объекта")
            target = _safe_member_path(destination, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o700)
                continue
            if not member.isfile():
                raise BackupSecurityError("Архив содержит неподдерживаемый объект")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise BackupSecurityError("Не удалось прочитать файл из архива")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=CHUNK_SIZE)
            target.chmod(0o600)


def _validate_extracted_manifest(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupSecurityError("В резервной копии отсутствует manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackupSecurityError("Manifest резервной копии повреждён") from error
    if int(manifest.get("manifest_version") or 0) != 1:
        raise BackupSecurityError("Версия manifest не поддерживается")
    if manifest.get("secrets_included") is not False:
        raise BackupSecurityError("Manifest не подтверждает исключение секретов")
    expected: dict[str, tuple[str, int]] = {}
    for item in manifest.get("files") or []:
        try:
            name = str(item["path"])
            digest = str(item["sha256"])
            size = int(item["size"])
        except (KeyError, TypeError, ValueError) as error:
            raise BackupSecurityError("Manifest содержит некорректную запись") from error
        _safe_member_path(root, name)
        if name in expected:
            raise BackupSecurityError("Manifest содержит повторяющийся путь")
        expected[name] = (digest, size)
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() != "manifest.json"
    }
    if actual_paths != set(expected):
        raise BackupSecurityError("Состав файлов не соответствует manifest")
    for name, (expected_digest, expected_size) in expected.items():
        path = _safe_member_path(root, name)
        if path.is_symlink() or not path.is_file():
            raise BackupSecurityError("Файл из manifest отсутствует")
        digest, size = _sha256_file(path)
        if size != expected_size or not hmac.compare_digest(digest, expected_digest):
            raise BackupSecurityError(f"Файл {name} не прошёл проверку целостности")
    return manifest


def verify_encrypted_backup(path: str | Path) -> BackupMetadata:
    source = Path(path)
    with tempfile.TemporaryDirectory(prefix="dlc-backup-verify-") as temporary_name:
        temporary = Path(temporary_name)
        payload = temporary / "payload.tar.gz"
        metadata = decrypt_backup_to_file(source, payload)
        extracted = temporary / "extracted"
        _extract_tar_safely(payload, extracted)
        _validate_extracted_manifest(extracted)
        return metadata


def create_encrypted_backup(
    *,
    database_url: str | None = None,
    storage_dir: str | Path | None = None,
    backup_dir: str | Path | None = None,
) -> BackupResult:
    database_path = _sqlite_database_path(database_url or settings.database_url)
    storage_path = Path(storage_dir or settings.storage_dir).resolve()
    output_dir = Path(backup_dir or settings.backup_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = output_dir / f"legal_concierge_{timestamp}{BACKUP_SUFFIX}"
    if target.exists():
        target = output_dir / f"legal_concierge_{timestamp}_{os.getpid()}{BACKUP_SUFFIX}"

    with tempfile.TemporaryDirectory(prefix="dlc-backup-build-") as temporary_name:
        temporary = Path(temporary_name)
        contents = temporary / "contents"
        contents.mkdir()
        database_target = contents / "database" / database_path.name
        _snapshot_sqlite(database_path, database_target)
        _copy_storage(storage_path, contents / "storage")
        manifest = _write_manifest(contents, database_name=database_path.name)
        payload = temporary / "payload.tar.gz"
        _build_tar_payload(contents, payload)
        metadata = encrypt_backup_payload(payload, target)

    verified = verify_encrypted_backup(target)
    return BackupResult(
        path=str(target),
        key_id=metadata.key_id,
        created_at=metadata.created_at,
        plaintext_sha256=metadata.plaintext_sha256,
        plaintext_size=metadata.plaintext_size,
        encrypted_size=target.stat().st_size,
        verified=verified.verified,
        files_count=len(manifest["files"]),
    )


def extract_encrypted_backup(path: str | Path, destination: str | Path) -> BackupMetadata:
    source = Path(path).resolve()
    destination_path = Path(destination).resolve()
    if destination_path.exists() and any(destination_path.iterdir()):
        raise BackupSecurityError("Каталог восстановления должен быть пустым")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".dlc-restore-",
        dir=destination_path.parent,
    ) as temporary_name:
        temporary = Path(temporary_name)
        payload = temporary / "payload.tar.gz"
        metadata = decrypt_backup_to_file(source, payload)
        validated = temporary / "validated"
        _extract_tar_safely(payload, validated)
        _validate_extracted_manifest(validated)
        if destination_path.exists():
            destination_path.rmdir()
        os.replace(validated, destination_path)
        try:
            destination_path.chmod(0o700)
        except OSError:
            pass
        return metadata


def cleanup_encrypted_backups(
    backup_dir: str | Path | None = None,
    *,
    retention_days: int | None = None,
) -> int:
    root = Path(backup_dir or settings.backup_dir)
    if not root.exists() or root.is_symlink():
        return 0
    days = max(1, int(retention_days or settings.backup_retention_days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed = 0
    for path in root.glob(f"*{BACKUP_SUFFIX}"):
        if path.is_symlink() or not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
