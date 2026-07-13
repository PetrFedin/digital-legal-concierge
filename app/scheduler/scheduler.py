from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.db.session import AsyncSessionLocal
from app.domain.notifications.notification_sender import NotificationSender
from app.scheduler.jobs import SchedulerJobs

logger = logging.getLogger(__name__)


class AppScheduler:
    def __init__(self, interval_seconds: int = 300):
        self.interval_seconds = max(60, interval_seconds)
        self._stop_event = asyncio.Event()

    async def run_once(self) -> dict:
        started_at = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            try:
                result = await SchedulerJobs(db).run_all()
                result["sent_notifications"] = await NotificationSender(db).send_pending()
                await db.commit()
                result["started_at"] = started_at.isoformat()
                result["finished_at"] = datetime.now(timezone.utc).isoformat()
                return result
            except Exception:
                await db.rollback()
                logger.exception("Scheduler cycle failed")
                raise

    async def run_forever(self) -> None:
        backoff = 5
        while not self._stop_event.is_set():
            try:
                result = await self.run_once()
                logger.info("Scheduler cycle completed: %s", result)
                backoff = 5
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler will retry in %s seconds", backoff)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 300)

    def stop(self) -> None:
        self._stop_event.set()
