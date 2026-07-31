from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL, make_url

from app.config import settings
from app.security.backup_encryption import (
    BackupSecurityError,
    extract_encrypted_backup,
)
from app.security.backup_restore_assessment import require_backup_restorable
from app.security.backup_restore_fence import backup_maintenance_lock
from app.security.backup_service import (
    POSTGRES_COMMAND_TIMEOUT_SECONDS,
    POSTGRES_DUMP_FORMAT,
    POSTGRES_DUMP_NAME,
    _postgres_environment,
    _safe_tool,
    _sanitized_tool_error,
    _write_pgpass,
)

_ALLOWED_STAGING_SUFFIXES = (
    "_staging",
    "_restore",
    "_drill",
    "_test",
    "-staging",
    "-restore",
    "-drill",
    "-test",
)
_FORBIDDEN_DATABASES = {"postgres", "template0", "template1"}
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class PostgreSQLRestoreError(BackupSecurityError):
    pass


@dataclass(frozen=True)
class PostgreSQLRestoreResult:
    archive: str
    destination: str
    database: str
    restored_revision: str
    expected_revision: str | None
    schema_current: bool
    restored_relations: int
    verified: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _postgres_url(value: str, *, label: str) -> URL:
    try:
        url = make_url(str(value))
    except Exception as error:
        raise PostgreSQLRestoreError(f"Некорректный {label} PostgreSQL URL") from error
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        raise PostgreSQLRestoreError(f"{label} должен использовать PostgreSQL")
    if not url.database:
        raise PostgreSQLRestoreError(f"В {label} отсутствует имя базы данных")
    return url


def _endpoint(url: URL) -> tuple[str, int, str]:
    # Database identity does not depend on which login is used. Comparing the
    # username would allow the same production database through another role.
    return (
        str(url.host or "localhost").lower().rstrip("."),
        int(url.port or 5432),
        str(url.database or ""),
    )


def validate_staging_target(
    target_database_url: str,
    *,
    confirmed_database: str,
    production_database_url: str | None = None,
) -> URL:
    target = _postgres_url(target_database_url, label="target")
    database = str(target.database)
    confirmation = str(confirmed_database or "").strip()
    if confirmation != database:
        raise PostgreSQLRestoreError(
            "Подтверждение имени staging-базы не совпадает с target URL"
        )

    production_value = str(production_database_url or settings.database_url)
    try:
        production = make_url(production_value)
    except Exception:
        production = None
    if (
        production is not None
        and production.get_backend_name() in {"postgresql", "postgres"}
        and _endpoint(target) == _endpoint(production)
    ):
        raise PostgreSQLRestoreError(
            "Восстановление в настроенную рабочую PostgreSQL базу запрещено"
        )

    lowered = database.lower()
    if lowered in _FORBIDDEN_DATABASES:
        raise PostgreSQLRestoreError("Системная PostgreSQL база не может быть target")
    if not lowered.endswith(_ALLOWED_STAGING_SUFFIXES):
        raise PostgreSQLRestoreError(
            "Имя target-базы должно явно оканчиваться на staging, restore, drill или test"
        )
    return target


def _run_capture(
    command: list[str],
    *,
    environment: dict[str, str],
    password: str | None,
) -> str:
    try:
        result = subprocess.run(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=POSTGRES_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise PostgreSQLRestoreError(
            f"{Path(command[0]).name} превысил лимит времени выполнения"
        ) from error
    except OSError as error:
        raise PostgreSQLRestoreError(
            f"Не удалось запустить {Path(command[0]).name}"
        ) from error
    if result.returncode != 0:
        raise _sanitized_tool_error(
            tool=Path(command[0]).name,
            result=result,
            password=password,
        )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _psql_scalar(
    sql: str,
    *,
    environment: dict[str, str],
    password: str | None,
) -> str:
    return _run_capture(
        [
            _safe_tool("psql"),
            "--no-password",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            f"--command={sql}",
        ],
        environment=environment,
        password=password,
    )


def _user_relations(
    *,
    environment: dict[str, str],
    password: str | None,
) -> int:
    value = _psql_scalar(
        "SELECT count(*) FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' "
        "AND c.relkind IN ('r','p','v','m','S','f');",
        environment=environment,
        password=password,
    )
    try:
        return int(value)
    except ValueError as error:
        raise PostgreSQLRestoreError(
            "Не удалось определить пустоту target PostgreSQL базы"
        ) from error


def _manifest_database(destination: Path) -> Path:
    manifest_path = destination / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PostgreSQLRestoreError("В staging-каталоге отсутствует manifest.json")
    raw = manifest_path.read_bytes()
    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        raise PostgreSQLRestoreError("Manifest имеет некорректный размер")
    try:
        manifest = json.loads(raw.decode("utf-8"))
        database = manifest["database"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise PostgreSQLRestoreError("Manifest PostgreSQL backup повреждён") from error
    if database != {
        "engine": "postgresql",
        "file": f"database/{POSTGRES_DUMP_NAME}",
        "format": POSTGRES_DUMP_FORMAT,
    }:
        raise PostgreSQLRestoreError(
            "Архив не содержит поддерживаемый PostgreSQL custom dump"
        )
    dump = destination / "database" / POSTGRES_DUMP_NAME
    resolved_destination = destination.resolve()
    resolved_dump = dump.resolve()
    try:
        resolved_dump.relative_to(resolved_destination)
    except ValueError as error:
        raise PostgreSQLRestoreError("Путь PostgreSQL dump выходит за staging") from error
    if dump.is_symlink() or not dump.is_file():
        raise PostgreSQLRestoreError("PostgreSQL dump отсутствует или небезопасен")
    with dump.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise PostgreSQLRestoreError("PostgreSQL dump имеет неизвестный формат")
    return dump


def _expected_revision() -> str | None:
    try:
        configuration = Config("alembic.ini")
        return ScriptDirectory.from_config(configuration).get_current_head()
    except Exception:
        return None


def restore_postgresql_staging(
    archive: str | Path,
    destination: str | Path,
    *,
    target_database_url: str,
    confirmed_database: str,
    production_database_url: str | None = None,
) -> PostgreSQLRestoreResult:
    target = validate_staging_target(
        target_database_url,
        confirmed_database=confirmed_database,
        production_database_url=production_database_url,
    )
    destination_path = Path(destination)

    with backup_maintenance_lock():
        require_backup_restorable(archive)
        metadata = extract_encrypted_backup(archive, destination_path)
        dump = _manifest_database(destination_path)

        with tempfile.TemporaryDirectory(prefix="dlc-restore-auth-") as temporary_name:
            temporary = Path(temporary_name)
            temporary.chmod(0o700)
            pgpass = _write_pgpass(target, temporary)
            try:
                environment = _postgres_environment(target, pgpass)
                if _user_relations(
                    environment=environment,
                    password=target.password,
                ) != 0:
                    raise PostgreSQLRestoreError(
                        "Target PostgreSQL база должна быть полностью пустой"
                    )
                command = [
                    _safe_tool("pg_restore"),
                    "--single-transaction",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "--no-password",
                    f"--dbname={target.database}",
                    str(dump),
                ]
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
                    raise PostgreSQLRestoreError(
                        "pg_restore превысил лимит времени выполнения"
                    ) from error
                except OSError as error:
                    raise PostgreSQLRestoreError("Не удалось запустить pg_restore") from error
                if result.returncode != 0:
                    raise _sanitized_tool_error(
                        tool="pg_restore",
                        result=result,
                        password=target.password,
                    )

                restored_revision = _psql_scalar(
                    "SELECT version_num FROM alembic_version LIMIT 1;",
                    environment=environment,
                    password=target.password,
                )
                if not restored_revision:
                    raise PostgreSQLRestoreError(
                        "В восстановленной базе отсутствует Alembic revision"
                    )
                relations = _user_relations(
                    environment=environment,
                    password=target.password,
                )
                if relations <= 0:
                    raise PostgreSQLRestoreError(
                        "Восстановленная PostgreSQL схема пуста"
                    )
            finally:
                if pgpass is not None:
                    pgpass.unlink(missing_ok=True)

    expected = _expected_revision()
    return PostgreSQLRestoreResult(
        archive=str(Path(archive)),
        destination=str(destination_path),
        database=str(target.database),
        restored_revision=restored_revision,
        expected_revision=expected,
        schema_current=bool(expected and restored_revision == expected),
        restored_relations=relations,
        verified=metadata.verified,
    )


__all__ = [
    "PostgreSQLRestoreError",
    "PostgreSQLRestoreResult",
    "restore_postgresql_staging",
    "validate_staging_target",
]
