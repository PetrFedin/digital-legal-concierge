from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self.db.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalars().first()

    async def get_or_create_from_telegram(
        self,
        *,
        telegram_id: int,
        telegram_username: str | None = None,
        full_name: str | None = None,
    ) -> User:
        user = await self._get_by_telegram_id(telegram_id)
        if user is None:
            candidate = User(
                telegram_id=telegram_id,
                telegram_username=telegram_username,
                full_name=full_name,
            )
            try:
                # The savepoint keeps the outer request transaction usable when
                # another Telegram update creates the same user concurrently.
                async with self.db.begin_nested():
                    self.db.add(candidate)
                    await self.db.flush()
                user = candidate
            except IntegrityError:
                user = await self._get_by_telegram_id(telegram_id)
                if user is None:
                    raise

        if telegram_username:
            user.telegram_username = telegram_username
        if full_name:
            user.full_name = full_name
        await self.db.flush()
        return user
