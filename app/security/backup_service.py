from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from sqlalchemy.engine import URL, make_url

from app.config import settings
from app.security.backup_encryption import (
    BACKUP_SUFFIX,
    BackupResult,
    BackupSecurityError,
    _build_tar_payload,
    _copy_storage,
    _manifest_entries,
    _sha256_file,
    create_encrypted_backup as create_sqlite_encrypted_backup,
    encrypt_backup_payload,
    verify_encrypted_backup,
)

POSTGRES_DUMP_NAME = "database.dump"
POSTGRES_DUMP_FORMAT = "pg_dump_custom"
POSTGRES_COMMAND_TIMEOUT_SECONDS = 300
_SUPPORTED_POSTGRES_BACKENDS = {"postgresql", "postgres"}
_LIBPQ_ENV_KEYS = {
    "PGAPPNAME",
    "PGCONNECT_TIMEOUT",
    "PGDATABASE",
    "PGHOST",
    "PGOPTIONS",
    "PGPASSFILE",
    "PGPASSWORD",
    "PGPORT",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGSSLCERT",
    "PGSSLKEY",
    "PGSSLMODE",
    "PGSSLROOTCERT",
    "PGUSER",
}


def _query_value(query: Mapping[str, object], name: str) -> str | None:
    value = query.get(name)
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        if not value:
            return None
        value = value[-1]
    result = str(value).strip()
    return result or None


def _escape_pgpass(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace(":", "\\:")


def _postgres_environment(url: URL, pgpass_file: Path | None) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _LIBPQ_ENV_KEYS
    }
    query = dict(url.query)
    if url.host:
        environment["PGHOST"] = str(url.host)
    if url.port:
        environment["PGPORT"] = str(url.port)
    if url.database:
        environment["PGDATABASE"] = str(url.database)
    if url.username:
        environment["PGUSER"] = str(url.username)
    if pgpass_file is not None:
        environment["PGPASSFILE"] = str(pgpass_file)
    environment["PGAPPNAME"] = "digital-legal-concierge-backup"
    environment["PGCONNECT_TIMEOUT"] = _query_value(query, "connect_timeout") or "10"

    sslmode = _query_value(query, "sslmode")
    asyncpg_ssl = _query_value(query, "ssl")
    if sslmode:
        environment["PGSSLMODE"] = sslmode
    elif asyncpg_ssl:
        normalized = asyncpg_ssl.lower()
        environment["PGSSLMODE"] = {
            "true": "require",
            "1": "require",
            "yes": "require",
            "false": "disable",
            "0": "disable",
            "no": "disable",
        }.get(normalized, normalized)

    for query_name, env_name in (
        ("sslrootcert", "PGSSLROOTCERT"),
        ("sslcert", "PGSSLCERT"),
        ("sslkey", "PGSSLKEY"),
        ("options", "PGOPTIONS"),
    ):
        value = _query_value(query, query_name)
        if value:
            environment[env_name] = value
    return environment


def _write_pgpass(url: URL, directory: Path) -> Path | None:
    if not url.password:
        return None
    host = url.host or "localhost"
    port = url.port or 5432
    database = url.database or "*"
    username = url.username or "*"
    path = directory / ".pgpass"
    path.write_text(
        ":".join(
            _escape_pgpass(value)
            for value in (host, port, database, username, url.password)
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _safe_tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise BackupSecurityError(
            f"Для PostgreSQL backup требуется установленная утилита {name}"
        )
    return executable


def _sanitized_tool_error(
    *,
    tool: str,
    result: subprocess.CompletedProcess[bytes],
    password: str | None,
) -> BackupSecurityError:
    message = result.stderr.decode("utf-8", errors="replace").strip()
    if password:
        message = message.replace(password, "[REDACTED]")
    message = " ".join(message.split())[:600]
    suffix = f": {message}" if message else ""
    return BackupSecurityError(
        f"{tool} завершился с кодом {result.returncode}{suffix}"
    )


def _run_postgres_tool(
    command: list[str],
    *,
    environment: dict[str, str],
    password: str | None,
) -> None:
    try:
        result = subprocess.run(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=POSTGRES_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise BackupSecurityError(
            f"{Path(command[0]).name} превысил лимит времени выполнения"
        ) from error
    except OSError as error:
        raise BackupSecurityError(
            f"Не удалось запустить {Path(command[0]).name}"
        ) from error
    if result.returncode != 0:
        raise _sanitized_tool_error(
            tool=Path(command[0]).name,
            result=result,
            password=password,
        )


def _snapshot_postgresql(database_url: str, target: Path, temporary: Path) -> None:
    url = make_url(database_url)
    if url.get_backend_name() not in _SUPPORTED_POSTGRES_BACKENDS:
        raise BackupSecurityError("Database URL не является PostgreSQL")
    if not url.database:
        raise BackupSecurityError("В PostgreSQL URL отсутствует имя базы данных")

    target.parent.mkdir(parents=True, exist_ok=True)
    pgpass_file = _write_pgpass(url, temporary)
    try:
        environment = _postgres_environment(url, pgpass_file)
        pg_dump = _safe_tool("pg_dump")
        pg_restore = _safe_tool("pg_restore")

        _run_postgres_tool(
            [
                pg_dump,
                "--format=custom",
                "--compress=6",
                "--no-owner",
                "--no-privileges",
                "--no-password",
                f"--file={target}",
            ],
            environment=environment,
            password=url.password,
        )
        if target.is_symlink() or not target.is_file():
            raise BackupSecurityError("pg_dump не создал ожидаемый файл")
        target.chmod(0o600)
        if target.stat().st_size < 5:
            raise BackupSecurityError("PostgreSQL dump пуст или усечён")
        with target.open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise BackupSecurityError("PostgreSQL dump имеет неизвестный формат")
        _sha256_file(target)
        _run_postgres_tool(
            [pg_restore, "--list", str(target)],
            environment=environment,
            password=url.password,
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        if pgpass_file is not None:
            pgpass_file.unlink(missing_ok=True)


def _write_provider_manifest(
    root: Path,
    *,
    database_engine: str,
    database_name: str,
    database_format: str,
) -> dict[str, object]:
    restore_dir = root / "restore"
    restore_dir.mkdir(parents=True, exist_ok=True)
    if database_engine == "postgresql":
        restore_database = (
            "Создайте пустую staging-базу и выполните:\n"
            f"pg_restore --no-owner --no-privileges --exit-on-error "
            f"--dbname=<STAGING_DATABASE_URL> database/{database_name}\n"
        )
    else:
        restore_database = (
            f"После остановки сервиса перенесите database/{database_name} "
            "по утверждённой staging-процедуре.\n"
        )
    readme = restore_dir / "README.txt"
    readme.write_text(
        "Проверенная staging-копия. Не заменяйте рабочие данные при запущенном сервисе.\n"
        + restore_database
        + "Каталог storage/ переносится только после проверки базы и прав доступа.\n"
        "Секреты окружения намеренно не входят в архив и восстанавливаются из "
        "внешнего secret manager.\n",
        encoding="utf-8",
    )
    readme.chmod(0o600)
    manifest: dict[str, object] = {
        "manifest_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": {
            "engine": database_engine,
            "file": f"database/{database_name}",
            "format": database_format,
        },
        "storage": {
            "root": "storage",
            "excluded_directories": [".incoming", "quarantine"],
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


def _safe_output_dir(backup_dir: str | Path | None) -> Path:
    output_dir = Path(backup_dir or settings.backup_dir)
    if output_dir.exists() and (output_dir.is_symlink() or not output_dir.is_dir()):
        raise BackupSecurityError("Каталог резервных копий имеет небезопасный тип")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    return output_dir.resolve()


def _target_path(output_dir: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = output_dir / f"legal_concierge_{timestamp}{BACKUP_SUFFIX}"
    if target.exists():
        target = output_dir / (
            f"legal_concierge_{timestamp}_{os.getpid()}{BACKUP_SUFFIX}"
        )
    return target


def create_provider_encrypted_backup(
    *,
    database_url: str | None = None,
    storage_dir: str | Path | None = None,
    backup_dir: str | Path | None = None,
) -> BackupResult:
    resolved_database_url = str(database_url or settings.database_url)
    url = make_url(resolved_database_url)
    if url.get_backend_name() == "sqlite":
        return create_sqlite_encrypted_backup(
            database_url=resolved_database_url,
            storage_dir=storage_dir,
            backup_dir=backup_dir,
        )
    if url.get_backend_name() not in _SUPPORTED_POSTGRES_BACKENDS:
        raise BackupSecurityError(
            f"Автоматический backup не поддерживает СУБД {url.get_backend_name()}"
        )

    storage_path = Path(storage_dir or settings.storage_dir).resolve()
    output_dir = _safe_output_dir(backup_dir)
    target = _target_path(output_dir)

    with tempfile.TemporaryDirectory(prefix="dlc-backup-build-") as temporary_name:
        temporary = Path(temporary_name)
        temporary.chmod(0o700)
        contents = temporary / "contents"
        contents.mkdir(mode=0o700)
        database_target = contents / "database" / POSTGRES_DUMP_NAME
        _snapshot_postgresql(resolved_database_url, database_target, temporary)
        _copy_storage(storage_path, contents / "storage")
        manifest = _write_provider_manifest(
            contents,
            database_engine="postgresql",
            database_name=POSTGRES_DUMP_NAME,
            database_format=POSTGRES_DUMP_FORMAT,
        )
        payload = temporary / "payload.tar.gz"
        _build_tar_payload(contents, payload)
        metadata = encrypt_backup_payload(payload, target)

    try:
        verified = verify_encrypted_backup(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
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


__all__ = [
    "POSTGRES_DUMP_FORMAT",
    "POSTGRES_DUMP_NAME",
    "create_provider_encrypted_backup",
]
