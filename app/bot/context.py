from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_service import CaseService
from app.domain.users.user_service import UserService


class BotContextService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_service = UserService(db)
        self.case_service = CaseService(db)

    async def get_user_from_message(self, message: Message):
        user = message.from_user
        return await self.user_service.get_or_create_from_telegram(
            telegram_id=user.id,
            telegram_username=user.username,
            full_name=user.full_name,
        )

    async def get_user_from_callback(self, callback: CallbackQuery):
        user = callback.from_user
        return await self.user_service.get_or_create_from_telegram(
            telegram_id=user.id,
            telegram_username=user.username,
            full_name=user.full_name,
        )

    async def get_or_create_active_case_for_user(self, user):
        return await self.case_service.get_or_create_active_case_for_user(user)
