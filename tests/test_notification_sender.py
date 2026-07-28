from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.notifications.notification_sender import NotificationSender
from app.models import Base
from app.models.notification import Notification
from app.models.user import User


class FakeBot:
    def __init__(self):
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str):
        self.messages.append((chat_id, text))


@pytest.fixture
async def sender_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'notification-sender.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_notification(
    session,
    *,
    suffix: int,
    recipient: str = "client",
    blocked: bool = False,
):
    user = User(
        telegram_id=999_500 + suffix,
        full_name=f"Клиент доставки {suffix}",
        is_blocked=blocked,
    )
    session.add(user)
    await session.flush()
    notification = Notification(
        user_id=user.id,
        event_code="PAYMENT_REMINDER",
        title=recipient,
        text=f"Сообщение {suffix}",
        status="PENDING",
        is_sent=False,
    )
    session.add(notification)
    await session.commit()
    return user, notification


@pytest.mark.asyncio
async def test_sender_without_bot_does_not_claim_delivery(sender_db):
    async with sender_db() as session:
        _, notification = await seed_notification(session, suffix=1)

        sent = await NotificationSender(session, bot=None).send_pending()
        await session.commit()

        await session.refresh(notification)
        assert sent == 0
        assert notification.status == "PENDING"
        assert notification.is_sent is False


@pytest.mark.asyncio
async def test_client_notification_is_sent_before_status_changes(sender_db):
    async with sender_db() as session:
        user, notification = await seed_notification(session, suffix=2)
        bot = FakeBot()

        sent = await NotificationSender(session, bot=bot).send_pending()
        await session.commit()

        await session.refresh(notification)
        assert sent == 1
        assert bot.messages == [(user.telegram_id, notification.text)]
        assert notification.status == "SENT"
        assert notification.is_sent is True


@pytest.mark.asyncio
async def test_blocked_client_is_failed_without_delivery(sender_db):
    async with sender_db() as session:
        _, notification = await seed_notification(
            session,
            suffix=3,
            blocked=True,
        )
        bot = FakeBot()

        sent = await NotificationSender(session, bot=bot).send_pending()
        await session.commit()

        await session.refresh(notification)
        assert sent == 0
        assert bot.messages == []
        assert notification.status == "FAILED"
        assert notification.is_sent is False


@pytest.mark.asyncio
async def test_non_client_recipient_is_not_falsely_marked_sent(sender_db):
    async with sender_db() as session:
        _, notification = await seed_notification(
            session,
            suffix=4,
            recipient="lawyer",
        )
        bot = FakeBot()

        sent = await NotificationSender(session, bot=bot).send_pending()
        await session.commit()

        await session.refresh(notification)
        assert sent == 0
        assert bot.messages == []
        assert notification.status == "PENDING"
        assert notification.is_sent is False
