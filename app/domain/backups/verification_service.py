from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from app.config import settings
from app.db.migrations import run_database_migrations


SUPPORTED_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".bak"}
REQUIRED_TABLES = {
    "alembic_version",
    "users",
    "lawyers",
    "cases",
    "payments",
    "consultations",
    "consultation_slots",
    "notifications",
    "audit_logs",
    "system_settings",
}


class BackupVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupVerificationResult:
    ok: bool
    source_name: str
    source_path: str
    source_size: int
    source_sha256: str
    verified_at: str
    integrity_result: str
    alembic_revision: str | None
    table_count: int
    required_tables_present: list[str]
    missing_tables: list[str]
    row_counts: dict[str, int]
    migrated_copy_path: str | None = None
    error: str | None = None


class BackupVerificationService:
    def __init__(self, *, storage_dir: str | Path | None = None):
        self.storage_root = Path(storage_dir or settings.storage_dir).expanduser()
        self.backup_dir = self.storage_root / "backups"
        self.manifest_dir = self.backup_dir / "manifests"
        self.staging_dir = self.storage_root / "restore_staging"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _database_path(database_url: str | None = None) -> Path:
        url = make_url(str(database_url or settings.database_url))
        if not url.drivername.startswith("sqlite"):
            raise BackupVerificationError(
                "Автоматическая проверка файловой копии доступна только для SQLite. "
                "Для PostgreSQL используйте pg_dump/pg_restore в отдельном контуре."
            )
        if not url.database or url.database == ":memory:":
            raise BackupVerificationError(
                "Нельзя создать файловую резервную копию для in-memory SQLite"
            )
        path = Path(url.database).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return path

    @staticmethod
    def _sqlite_url(path: Path) -> str:
        return f"sqlite+aiosqlite:///{path.resolve()}"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _safe_filename(value: str) -> str:
        name = Path(str(value or "")).name
        if not name or name in {".", ".."}:
            raise BackupVerificationError("Некорректное имя резервной копии")
        if Path(name).suffix.lower() not in SUPPORTED_SQLITE_SUFFIXES:
            raise BackupVerificationError(
                "Поддерживаются SQLite-файлы: .db, .sqlite, .sqlite3, .bak"
            )
        return name

    def resolve_backup(self, filename: str) -> Path:
        name = self._safe_filename(filename)
        path = (self.backup_dir / name).resolve()
        if path.parent != self.backup_dir.resolve():
            raise BackupVerificationError("Выход за каталог резервных копий запрещён")
        if not path.is_file():
            raise BackupVerificationError("Резервная копия не найдена")
        return path

    @staticmethod
    def _online_sqlite_backup(source_path: Path, target_path: Path) -> None:
        if not source_path.is_file():
            raise BackupVerificationError(
                f"Рабочая база SQLite не найдена: {source_path}"
            )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(
            f"file:{source_path}?mode=ro",
            uri=True,
            timeout=30,
        ) as source, sqlite3.connect(target_path, timeout=30) as target:
            source.backup(target, pages=512, sleep=0.01)
            target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            target.commit()
        with target_path.open("rb+") as file_handle:
            os.fsync(file_handle.fileno())

    @staticmethod
    def _integrity_check(path: Path) -> str:
        try:
            with sqlite3.connect(
                f"file:{path}?mode=ro",
                uri=True,
                timeout=30,
            ) as connection:
                rows = connection.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.DatabaseError as error:
            raise BackupVerificationError(
                f"SQLite не смог открыть резервную копию: {error}"
            ) from error
        result = "; ".join(str(row[0]) for row in rows)
        if rows != [("ok",)]:
            raise BackupVerificationError(
                f"Проверка целостности SQLite завершилась ошибкой: {result}"
            )
        return result

    @staticmethod
    def _smoke_check(path: Path) -> dict[str, Any]:
        try:
            with sqlite3.connect(path, timeout=30) as connection:
                table_rows = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' ORDER BY name"
                ).fetchall()
                tables = {str(row[0]) for row in table_rows}
                missing = sorted(REQUIRED_TABLES - tables)
                revision_row = connection.execute(
                    "SELECT version_num FROM alembic_version LIMIT 1"
                ).fetchone() if "alembic_version" in tables else None
                row_counts: dict[str, int] = {}
                for table_name in sorted(
                    tables
                    & {
                        "users",
                        "lawyers",
                        "cases",
                        "payments",
                        "consultations",
                        "notifications",
                        "audit_logs",
                    }
                ):
                    row_counts[table_name] = int(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table_name}"'
                        ).fetchone()[0]
                    )
        except sqlite3.DatabaseError as error:
            raise BackupVerificationError(
                f"Smoke-проверка восстановленной базы не выполнена: {error}"
            ) from error
        if missing:
            raise BackupVerificationError(
                "После миграции отсутствуют обязательные таблицы: "
                + ", ".join(missing)
            )
        return {
            "tables": sorted(tables),
            "missing": missing,
            "revision": str(revision_row[0]) if revision_row else None,
            "row_counts": row_counts,
        }

    async def verify_file(
        self,
        source_path: Path,
        *,
        preserve_migrated_copy: Path | None = None,
    ) -> BackupVerificationResult:
        source_path = source_path.resolve()
        if not source_path.is_file():
            raise BackupVerificationError("Файл резервной копии не найден")
        source_size = source_path.stat().st_size
        if source_size <= 0:
            raise BackupVerificationError("Файл резервной копии пуст")
        source_hash = await asyncio.to_thread(self._sha256, source_path)

        work_dir = Path(tempfile.mkdtemp(prefix="dlc-restore-verify-"))
        migrated_path = work_dir / "restored.db"
        try:
            await asyncio.to_thread(shutil.copy2, source_path, migrated_path)
            integrity = await asyncio.to_thread(
                self._integrity_check,
                migrated_path,
            )
            await asyncio.to_thread(
                run_database_migrations,
                database_url=self._sqlite_url(migrated_path),
            )
            smoke = await asyncio.to_thread(self._smoke_check, migrated_path)

            final_copy: str | None = None
            if preserve_migrated_copy is not None:
                preserve_migrated_copy = preserve_migrated_copy.resolve()
                preserve_migrated_copy.parent.mkdir(parents=True, exist_ok=True)
                temporary_target = preserve_migrated_copy.with_suffix(
                    preserve_migrated_copy.suffix + ".tmp"
                )
                await asyncio.to_thread(
                    shutil.copy2,
                    migrated_path,
                    temporary_target,
                )
                os.replace(temporary_target, preserve_migrated_copy)
                final_copy = str(preserve_migrated_copy)

            return BackupVerificationResult(
                ok=True,
                source_name=source_path.name,
                source_path=str(source_path),
                source_size=source_size,
                source_sha256=source_hash,
                verified_at=datetime.now(timezone.utc).isoformat(),
                integrity_result=integrity,
                alembic_revision=smoke["revision"],
                table_count=len(smoke["tables"]),
                required_tables_present=sorted(
                    REQUIRED_TABLES & set(smoke["tables"])
                ),
                missing_tables=smoke["missing"],
                row_counts=smoke["row_counts"],
                migrated_copy_path=final_copy,
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _write_manifest(self, result: BackupVerificationResult) -> Path:
        manifest_path = self.manifest_dir / f"{result.source_name}.json"
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, manifest_path)
        return manifest_path

    async def create_verified_backup(
        self,
        *,
        database_url: str | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        source_path = self._database_path(database_url)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_label = "".join(
            character
            for character in str(label or "manual").strip().lower()
            if character.isalnum() or character in {"-", "_"}
        )[:40] or "manual"
        filename = f"legal_bot_{timestamp}_{safe_label}.db"
        final_path = self.backup_dir / filename
        temporary_path = self.backup_dir / f".{filename}.tmp"
        try:
            await asyncio.to_thread(
                self._online_sqlite_backup,
                source_path,
                temporary_path,
            )
            result = await self.verify_file(temporary_path)
            os.replace(temporary_path, final_path)
            final_result = BackupVerificationResult(
                **{
                    **asdict(result),
                    "source_name": final_path.name,
                    "source_path": str(final_path.resolve()),
                    "source_sha256": await asyncio.to_thread(
                        self._sha256,
                        final_path,
                    ),
                }
            )
            manifest_path = await asyncio.to_thread(
                self._write_manifest,
                final_result,
            )
            return {
                **asdict(final_result),
                "manifest_path": str(manifest_path),
            }
        finally:
            temporary_path.unlink(missing_ok=True)

    async def verify_backup(self, filename: str) -> dict[str, Any]:
        source = self.resolve_backup(filename)
        result = await self.verify_file(source)
        manifest_path = await asyncio.to_thread(self._write_manifest, result)
        return {**asdict(result), "manifest_path": str(manifest_path)}

    async def stage_restore(self, filename: str) -> dict[str, Any]:
        source = self.resolve_backup(filename)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        staging_name = f"staged_{timestamp}_{source.name}"
        staging_path = self.staging_dir / staging_name
        result = await self.verify_file(
            source,
            preserve_migrated_copy=staging_path,
        )
        staging_hash = await asyncio.to_thread(self._sha256, staging_path)
        return {
            **asdict(result),
            "staged_path": str(staging_path.resolve()),
            "staged_sha256": staging_hash,
            "restore_instruction": (
                "Остановите приложение, создайте копию текущей базы, "
                f"замените её файлом {staging_path.name}, затем запустите "
                "python scripts/migrate_db.py и приложение."
            ),
        }

    def list_backups(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(
            self.backup_dir.iterdir(),
            key=lambda item: item.stat().st_mtime if item.is_file() else 0,
            reverse=True,
        ):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SQLITE_SUFFIXES:
                continue
            manifest_path = self.manifest_dir / f"{path.name}.json"
            manifest = None
            if manifest_path.is_file():
                try:
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    manifest = None
            items.append(
                {
                    "filename": path.name,
                    "size": path.stat().st_size,
                    "modified_at": datetime.fromtimestamp(
                        path.stat().st_mtime,
                        tz=timezone.utc,
                    ).isoformat(),
                    "verified": bool(manifest and manifest.get("ok")),
                    "verified_at": (
                        manifest.get("verified_at") if manifest else None
                    ),
                    "sha256": (
                        manifest.get("source_sha256") if manifest else None
                    ),
                    "alembic_revision": (
                        manifest.get("alembic_revision") if manifest else None
                    ),
                    "manifest_path": (
                        str(manifest_path) if manifest_path.is_file() else None
                    ),
                }
            )
        return items
