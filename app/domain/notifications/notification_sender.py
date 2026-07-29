from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.user import User

logger = logging.getLogger(__name__)


class NotificationSender:
    def __init__(self, db: AsyncSession, bot: Bot | None = None):
        self.db = db
        self.bot = bot
        self._owns_bot = False

    async def _build_bot(self) -> Bot | None:
        if self.bot is not None:
            return self.bot
        if not settings.bot_token or settings.bot_token == "CHANGE_ME":
            return None
        self.bot = Bot(token=settings.bot_token)
        self._owns_bot = True
        return self.bot

    async def _resolve_missing_target(
        self,
        notification: Notification,
    ) -> int | None:
        recipient = notification.recipient_type or notification.title
        if recipient == "client":
            user = (
                await self.db.get(User, notification.user_id)
                if notification.user_id
                else None
            )
            if user is None and notification.case_id:
                case = await self.db.get(Case, notification.case_id)
                user = await self.db.get(User, case.client_id) if case else None
            return user.telegram_id if user else None

        if recipient == "lawyer" and notification.case_id:
            case = await self.db.get(Case, notification.case_id)
            lawyer_id = case.assigned_lawyer_id if case else None
            if not lawyer_id:
                consultation = (
                    await self.db.execute(
                        select(Consultation)
                        .where(Consultation.case_id == notification.case_id)
                        .where(Consultation.lawyer_id.is_not(None))
                        .order_by(
                            Consultation.scheduled_at.desc(),
                            Consultation.created_at.desc(),
                        )
                        .limit(1)
                    )
                ).scalars().first()
                lawyer_id = consultation.lawyer_id if consultation else None
            lawyer = await self.db.get(Lawyer, lawyer_id) if lawyer_id else None
            return lawyer.telegram_id if lawyer else None

        if recipient == "admin":
            admin = (
                await self.db.execute(
                    select(AdminUser)
                    .where(AdminUser.is_active.is_(True))
                    .where(AdminUser.telegram_id.is_not(None))
                    .order_by(AdminUser.id.asc())
                    .limit(1)
                )
            ).scalars().first()
            return admin.telegram_id if admin else None

        return None

    @staticmethod
    def _retry_delay(attempt_count: int) -> timedelta:
        minutes = min(5 * (2 ** max(attempt_count - 1, 0)), 360)
        return timedelta(minutes=minutes)

    def _mark_retry(
        self,
        notification: Notification,
        *,
        error: str,
        retry_after: timedelta | None = None,
    ) -> None:
        notification.status = "RETRY"
        notification.is_sent = False
        notification.last_error = error[:2000]
        notification.next_attempt_at = datetime.now(timezone.utc) + (
            retry_after or self._retry_delay(notification.attempt_count)
        )

    @staticmethod
    def _mark_failed(notification: Notification, *, error: str) -> None:
        notification.status = "FAILED"
        notification.is_sent = False
        notification.last_error = error[:2000]
        notification.next_attempt_at = None

    @staticmethod
    def _mark_sent(notification: Notification) -> None:
        notification.status = "SENT"
        notification.is_sent = True
        notification.last_error = None
        notification.next_attempt_at = None
        notification.sent_at = datetime.now(timezone.utc)

    async def send_pending(self, limit: int = 50) -> int:
        now = datetime.now(timezone.utc)
        notifications = list(
            (
                await self.db.execute(
                    select(Notification)
                    .where(Notification.status.in_(["PENDING", "RETRY"]))
                    .where(
                        or_(
                            Notification.next_attempt_at.is_(None),
                            Notification.next_attempt_at <= now,
                        )
                    )
                    .order_by(Notification.created_at.asc(), Notification.id.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
        )
        if not notifications:
            return 0

        bot = await self._build_bot()
        sent = 0
        try:
            for notification in notifications:
                notification.attempt_count = int(notification.attempt_count or 0) + 1

                if notification.target_chat_id is None:
                    notification.target_chat_id = await self._resolve_missing_target(
                        notification
                    )
                if notification.target_chat_id is None:
                    self._mark_failed(
                        notification,
                        error=(
                            "Не найден Telegram chat ID для получателя "
                            f"{notification.recipient_type or notification.title}"
                        ),
                    )
                    continue

                if bot is None:
                    self._mark_retry(
                        notification,
                        error="BOT_TOKEN не настроен; сообщение не отправлено",
                        retry_after=timedelta(minutes=15),
                    )
                    continue

                try:
                    await bot.send_message(
                        chat_id=notification.target_chat_id,
                        text=notification.text,
                    )
                except TelegramRetryAfter as error:
                    self._mark_retry(
                        notification,
                        error=str(error),
                        retry_after=timedelta(
                            seconds=max(int(error.retry_after), 1)
                        ),
                    )
                except TelegramBadRequest as error:
                    self._mark_failed(notification, error=str(error))
                except (
                    TelegramNetworkError,
                    TelegramServerError,
                    TimeoutError,
                    OSError,
                ) as error:
                    self._mark_retry(notification, error=str(error))
                except Exception as error:
                    logger.exception(
                        "Неожиданная ошибка отправки уведомления %s",
                        notification.id,
                    )
                    self._mark_retry(notification, error=str(error))
                else:
                    self._mark_sent(notification)
                    sent += 1

            await self.db.flush()
            return sent
        finally:
            if self._owns_bot and self.bot is not None:
                await self.bot.session.close()
                self.bot = None
                self._owns_bot = False
