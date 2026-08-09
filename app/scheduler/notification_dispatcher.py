from __future__ import annotations

import asyncio
import logging

from app.db.session import AsyncSessionLocal
from app.domain.notifications.notification_sender import NotificationSender

logger = logging.getLogger(__name__)

DEFAULT_NOTIFICATION_INTERVAL_SECONDS = 60
DEFAULT_NOTIFICATION_BATCH_SIZE = 100


class NotificationDispatcher:
    """Lightweight durable notification loop independent from heavy scheduler jobs.

    Legal/process mutations commit notification outbox rows first. This loop then
    delivers due rows with a fresh database session, so Telegram availability can
    never roll back a payment, document, consultation, or case-state mutation.
    """

    def __init__(
        self,
        *,
        interval_seconds: int = DEFAULT_NOTIFICATION_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_NOTIFICATION_BATCH_SIZE,
    ):
        self.interval_seconds = max(1, int(interval_seconds))
        self.batch_size = max(1, int(batch_size))
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self.last_result: dict[str, int] | None = None
        self.last_error_type: str | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run_once(self) -> dict[str, int]:
        async with AsyncSessionLocal() as db:
            try:
                result = await NotificationSender(db).send_pending(
                    limit=self.batch_size
                )
                await db.commit()
            except asyncio.CancelledError:
                await db.rollback()
                raise
            except Exception as error:
                await db.rollback()
                self.last_error_type = type(error).__name__
                logger.exception("notification_dispatch_cycle_failed")
                raise
        self.last_result = dict(result)
        self.last_error_type = None
        return self.last_result

    async def _run_loop(self) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            return
        while not stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Delivery rows keep their own retry/backoff state. A dispatcher
                # cycle error must not terminate future delivery attempts.
                pass
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    def start(self) -> asyncio.Task:
        if self.is_running:
            return self._task  # type: ignore[return-value]
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="notification-dispatcher",
        )
        return self._task

    async def stop(self) -> None:
        task = self._task
        stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stop_event = None
