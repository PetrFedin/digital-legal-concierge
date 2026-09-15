from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.intake_service import CalculationIntakeService
from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
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
        """Compatibility access to the currently selected active Case."""
        return await self.case_service.get_or_create_active_case_for_user(user)

    async def create_case_from_callback(
        self,
        *,
        user,
        callback: CallbackQuery,
        purpose: str,
        route: str | None = None,
        status="NEW",
        title: str | None = None,
    ):
        case = await self.case_service.create_case_for_operation(
            client=user,
            operation_key=f"telegram_callback:{callback.id}",
            purpose=purpose,
            route=route,
            status=status,
            title=title,
        )
        # PM-017: the Case binding itself is a meaningful calculator draft fact.
        # Create the PostgreSQL intake before the caller commits/presents B-003,
        # even when the client has not entered the first answer yet. Exact
        # callback redelivery remains idempotent because the intake is unique per
        # Case and ensure() returns the existing row.
        if purpose == "calculator_start" and str(case.status) in {
            CaseStatus.NEW.value,
            CaseStatus.CALCULATOR_STARTED.value,
        }:
            await CalculationIntakeService(self.db).ensure(case_id=int(case.id))
        return case

    async def create_case_from_message(
        self,
        *,
        user,
        message: Message,
        purpose: str,
        route: str | None = None,
        status="NEW",
        title: str | None = None,
    ):
        return await self.case_service.create_case_for_operation(
            client=user,
            operation_key=(
                f"telegram_message:{message.chat.id}:{message.message_id}"
            ),
            purpose=purpose,
            route=route,
            status=status,
            title=title,
        )
