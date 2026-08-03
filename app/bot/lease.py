from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config import settings
from app.db.session import engine

try:
    import fcntl
except ImportError:  # pragma: no cover - production images are Linux.
    fcntl = None


POSTGRES_TELEGRAM_LOCK_KEY = 0x444C4354424F5401
TELEGRAM_LOCK_FILE = ".telegram-polling.lock"


class TelegramPollingLeaseError(RuntimeError):
    pass


class TelegramPollingLease:
    """One Telegram polling consumer across all application instances."""

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

    async def acquire_once(self) -> bool:
        if (
            self.acquired
            or self._connection is not None
            or self._file_descriptor is not None
        ):
            raise TelegramPollingLeaseError("Telegram polling lease уже использован")
        if self.dialect_name == "postgresql":
            return await self._acquire_postgresql()
        if self.dialect_name == "sqlite":
            return await asyncio.to_thread(self._acquire_file)
        raise TelegramPollingLeaseError(
            f"Telegram polling lease не поддерживает {self.dialect_name}"
        )

    async def acquire_with_wait(
        self,
        *,
        timeout_seconds: int,
        retry_seconds: int,
    ) -> bool:
        timeout = max(0, int(timeout_seconds))
        retry = max(1, int(retry_seconds))
        deadline = time.monotonic() + timeout
        while True:
            if await self.acquire_once():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(retry, remaining))

    async def _acquire_postgresql(self) -> bool:
        connection = await self.db_engine.connect()
        try:
            acquired = bool(
                (
                    await connection.execute(
                        text("SELECT pg_try_advisory_lock(:lock_key)"),
                        {"lock_key": POSTGRES_TELEGRAM_LOCK_KEY},
                    )
                ).scalar_one()
            )
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
            raise TelegramPollingLeaseError("flock недоступен в текущей системе")
        root = self.lock_dir
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            raise TelegramPollingLeaseError(
                "Каталог Telegram lock имеет небезопасный тип"
            )
        root.mkdir(parents=True, exist_ok=True)
        path = root / TELEGRAM_LOCK_FILE
        if path.exists() and path.is_symlink():
            raise TelegramPollingLeaseError("Telegram lock является symlink")
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
                        {"lock_key": POSTGRES_TELEGRAM_LOCK_KEY},
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


__all__ = [
    "POSTGRES_TELEGRAM_LOCK_KEY",
    "TELEGRAM_LOCK_FILE",
    "TelegramPollingLease",
    "TelegramPollingLeaseError",
]
