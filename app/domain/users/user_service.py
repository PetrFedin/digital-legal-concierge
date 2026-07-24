from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _normalize_username(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().removeprefix("@").strip()
        return normalized or None

    @staticmethod
    def _normalize_full_name(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    async def get_or_create_from_telegram(
        self,
        *,
        telegram_id: int,
        telegram_username: str | None = None,
        full_name: str | None = None,
    ) -> User:
        """Return the Telegram user while preserving client-confirmed profile data.

        ``telegram_id`` is the stable identity key. Telegram username is synchronized on
        every interaction, including removal, because it is mutable public metadata.
        ``full_name`` is only used to fill an empty profile field; an existing value may
        have been corrected by the client or an administrator and must not be overwritten
        by a later Telegram display-name change.
        """
        if telegram_id <= 0:
            raise ValueError("telegram_id must be a positive integer")

        normalized_username = self._normalize_username(telegram_username)
        normalized_full_name = self._normalize_full_name(full_name)

        result = await self.db.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()

        if user is not None:
            user.telegram_username = normalized_username
            if not self._normalize_full_name(user.full_name) and normalized_full_name:
                user.full_name = normalized_full_name
            await self.db.flush()
            return user

        user = User(
            telegram_id=telegram_id,
            telegram_username=normalized_username,
            full_name=normalized_full_name,
        )
        self.db.add(user)
        await self.db.flush()
        return user
