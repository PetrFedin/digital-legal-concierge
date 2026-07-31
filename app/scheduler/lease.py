from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config import settings
from app.db.session import engine

try:
    import fcntl
except ImportError:  # pragma: no cover - production images are Linux.
    fcntl = None


# Stable signed 64-bit namespace for PostgreSQL session advisory locks.
POSTGRES_SCHEDULER_LOCK_KEY = 0x444C435343484544
SCHEDULER_LOCK_FILE = ".scheduler-cycle.lock"


class SchedulerLeaseError(RuntimeError):
    pass


class SchedulerCycleLease:
    """One active scheduler cycle across all application instances.

    PostgreSQL uses a session-level advisory lock, which is automatically
    released if the process or database connection dies. SQLite uses a
    non-blocking ``flock`` in the backup volume, which is already required to
    be shared by all instances that can operate on the same local database.
    """

    def __init__(
        self,
        *,
        db_engine: AsyncEngine | None = None,
        lock_dir: str | Path | None = None,
        dialect_name: str | None = None,
    ):
        self.db_engine = db_engine or engine
        self.lock_dir = Path(lock_dir or settings.backup_dir)
        self.dialect_name = dialect_name or self.db_engine.dialect.name
        self._connection: AsyncConnection | None = None
        self._file_descriptor: int | None = None
        self.acquired = False

    async def acquire(self) -> bool:
        if (
            self.acquired
            or self._connection is not None
            or self._file_descriptor is not None
        ):
            raise SchedulerLeaseError("Scheduler lease уже использован")
        if self.dialect_name == "postgresql":
            return await self._acquire_postgresql()
        if self.dialect_name == "sqlite":
            return await asyncio.to_thread(self._acquire_file)
        raise SchedulerLeaseError(
            f"Scheduler singleton lock не поддерживает {self.dialect_name}"
        )

    async def _acquire_postgresql(self) -> bool:
        connection = await self.db_engine.connect()
        try:
            result = await connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": POSTGRES_SCHEDULER_LOCK_KEY},
            )
            acquired = bool(result.scalar_one())
            # Avoid holding an idle transaction for the full cycle; the
            # advisory lock itself remains bound to this connection session.
            await connection.commit()
            if not acquired:
                await connection.close()
                return False
            self._connection = connection
            self.acquired = True
            return True
        except Exception:
            await connection.close()
            raise

    def _acquire_file(self) -> bool:
        if fcntl is None:
            raise SchedulerLeaseError("flock недоступен в текущей системе")
        root = self.lock_dir
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            raise SchedulerLeaseError("Каталог scheduler lock имеет небезопасный тип")
        root.mkdir(parents=True, exist_ok=True)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        path = root / SCHEDULER_LOCK_FILE
        if path.exists() and path.is_symlink():
            raise SchedulerLeaseError("Scheduler lock является symlink")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(descriptor)
                return False
            self._file_descriptor = descriptor
            self.acquired = True
            return True
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    async def assert_held(self) -> None:
        """Fail before a job if the process lost its singleton lease."""

        if not self.acquired:
            raise SchedulerLeaseError("Scheduler singleton lease не удерживается")
        if self._connection is not None:
            try:
                result = await self._connection.execute(
                    text(
                        "SELECT pg_advisory_lock_shared(:lock_key), "
                        "pg_advisory_unlock_shared(:lock_key)"
                    ),
                    {"lock_key": POSTGRES_SCHEDULER_LOCK_KEY},
                )
                result.first()
                await self._connection.commit()
            except Exception as error:
                self.acquired = False
                raise SchedulerLeaseError(
                    "Соединение PostgreSQL scheduler lease потеряно"
                ) from error
            return
        if self._file_descriptor is not None:
            try:
                await asyncio.to_thread(os.fstat, self._file_descriptor)
            except OSError as error:
                self.acquired = False
                raise SchedulerLeaseError(
                    "Файловый scheduler lease потерян"
                ) from error
            return
        raise SchedulerLeaseError("Scheduler singleton lease не имеет ресурса")

    async def release(self) -> None:
        connection = self._connection
        descriptor = self._file_descriptor
        self._connection = None
        self._file_descriptor = None
        was_acquired = self.acquired
        self.acquired = False

        if connection is not None:
            try:
                if was_acquired:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": POSTGRES_SCHEDULER_LOCK_KEY},
                    )
                    await connection.commit()
            finally:
                await connection.close()

        if descriptor is not None:
            await asyncio.to_thread(self._release_file, descriptor)

    @staticmethod
    def _release_file(descriptor: int) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    async def __aenter__(self) -> SchedulerCycleLease:
        if not await self.acquire():
            raise SchedulerLeaseError("Scheduler singleton lease занят")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.release()


__all__ = [
    "POSTGRES_SCHEDULER_LOCK_KEY",
    "SCHEDULER_LOCK_FILE",
    "SchedulerCycleLease",
    "SchedulerLeaseError",
]
