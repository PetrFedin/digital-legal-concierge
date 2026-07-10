from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.notification import Notification

class NotificationSender:
    def __init__(self, db: AsyncSession, bot=None):
        self.db = db
        self.bot = bot

    async def send_pending(self, limit: int = 50) -> int:
        result = await self.db.execute(select(Notification).where(Notification.status == "PENDING").limit(limit))
        notifications = list(result.scalars().all())
        sent = 0
        for notification in notifications:
            # В v3 это очередь уведомлений. Реальная отправка через Telegram подключается в bot runtime.
            notification.status = "SENT"
            notification.is_sent = True
            sent += 1
        await self.db.flush()
        return sent
