from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.notifications.notification_sender import NotificationSender
from app.scheduler.jobs import SchedulerJobs


logger = logging.getLogger(__name__)


class AppScheduler:
    def __init__(self, interval_seconds: int = 3600, *, bot=None):
        self.interval_seconds = interval_seconds
        self.bot = bot

    async def run_once(self) -> dict:
        async with AsyncSessionLocal() as db:
            jobs = SchedulerJobs(db)
            result = {
                "payment_reminders": await jobs.check_unpaid_payments(),
                "released_slots": await jobs.release_unpaid_consultation_slots(),
                "claim_deadlines": await jobs.check_claim_waiting_30_days(),
                "sent_notifications": await NotificationSender(
                    db,
                    bot=self.bot,
                ).send_pending(),
            }
            await db.commit()
            return result

    async def run_forever(self):
        owned_bot = None
        if (
            self.bot is None
            and settings.bot_token
            and settings.bot_token != "CHANGE_ME"
        ):
            owned_bot = Bot(token=settings.bot_token)
            self.bot = owned_bot

        try:
            while True:
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Scheduled legal concierge jobs failed")
                await asyncio.sleep(self.interval_seconds)
        finally:
            if owned_bot is not None:
                await owned_bot.session.close()
