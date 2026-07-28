from __future__ import annotations

import logging

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.user import User


logger = logging.getLogger(__name__)


class NotificationSender:
    """Deliver client notifications without inventing admin/lawyer routing.

    The current schema can resolve only a client through ``Notification.user_id``.
    Records addressed to ``admin`` or ``lawyer`` remain pending for a future
    recipient model instead of being falsely marked as delivered.
    """

    def __init__(self, db: AsyncSession, bot=None):
        self.db = db
        self.bot = bot

    async def send_pending(self, limit: int = 50) -> int:
        if self.bot is None:
            return 0

        rows = list(
            (
                await self.db.execute(
                    select(Notification, User)
                    .join(User, User.id == Notification.user_id)
                    .where(
                        Notification.status == "PENDING",
                        Notification.title == "client",
                        Notification.is_sent.is_(False),
                    )
                    .order_by(Notification.created_at.asc(), Notification.id.asc())
                    .limit(max(1, min(int(limit), 500)))
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )

        sent = 0
        for notification, user in rows:
            if user.is_blocked:
                notification.status = "FAILED"
                notification.is_sent = False
                continue
            try:
                await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=notification.text,
                )
            except TelegramRetryAfter:
                # Preserve PENDING so the scheduler can retry after Telegram's
                # requested delay. Stop this batch to avoid more rate-limit hits.
                break
            except (TelegramNetworkError, TelegramServerError):
                logger.warning(
                    "Telegram notification delivery is temporarily unavailable",
                    extra={"notification_id": notification.id},
                )
                break
            except (TelegramBadRequest, TelegramForbiddenError):
                notification.status = "FAILED"
                notification.is_sent = False
                logger.info(
                    "Telegram notification recipient is unavailable",
                    extra={
                        "notification_id": notification.id,
                        "user_id": user.id,
                    },
                )
                continue

            notification.status = "SENT"
            notification.is_sent = True
            sent += 1

        await self.db.flush()
        return sent
