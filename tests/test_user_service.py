from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.users.user_service import UserService
from app.models.user import User


@dataclass
class _ScalarResult:
    value: User | None

    def scalar_one_or_none(self) -> User | None:
        return self.value


class _FakeSession:
    def __init__(self, existing: User | None = None):
        self.existing = existing
        self.added: list[User] = []
        self.flush_count = 0

    async def execute(self, _statement):
        return _ScalarResult(self.existing)

    def add(self, value: User) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1


@pytest.mark.asyncio
async def test_creates_normalized_user_from_telegram() -> None:
    session = _FakeSession()
    service = UserService(session)  # type: ignore[arg-type]

    user = await service.get_or_create_from_telegram(
        telegram_id=123456,
        telegram_username="  @client_name  ",
        full_name="  Ivan   Ivanov  ",
    )

    assert user.telegram_id == 123456
    assert user.telegram_username == "client_name"
    assert user.full_name == "Ivan Ivanov"
    assert session.added == [user]
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_existing_confirmed_name_is_not_overwritten() -> None:
    existing = User(
        telegram_id=123456,
        telegram_username="old_name",
        full_name="Confirmed Client Name",
    )
    session = _FakeSession(existing)
    service = UserService(session)  # type: ignore[arg-type]

    user = await service.get_or_create_from_telegram(
        telegram_id=123456,
        telegram_username="@new_name",
        full_name="Telegram Display Name",
    )

    assert user is existing
    assert user.telegram_username == "new_name"
    assert user.full_name == "Confirmed Client Name"
    assert session.added == []
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_empty_existing_name_is_filled_once() -> None:
    existing = User(
        telegram_id=123456,
        telegram_username=None,
        full_name="   ",
    )
    session = _FakeSession(existing)
    service = UserService(session)  # type: ignore[arg-type]

    user = await service.get_or_create_from_telegram(
        telegram_id=123456,
        telegram_username=None,
        full_name="  Anna   Petrova ",
    )

    assert user.full_name == "Anna Petrova"
    assert user.telegram_username is None


@pytest.mark.asyncio
async def test_removed_telegram_username_is_cleared() -> None:
    existing = User(
        telegram_id=123456,
        telegram_username="previous_name",
        full_name="Client Name",
    )
    session = _FakeSession(existing)
    service = UserService(session)  # type: ignore[arg-type]

    user = await service.get_or_create_from_telegram(
        telegram_id=123456,
        telegram_username=None,
        full_name=None,
    )

    assert user.telegram_username is None
    assert user.full_name == "Client Name"


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram_id", [0, -1, -999])
async def test_rejects_non_positive_telegram_id(telegram_id: int) -> None:
    session = _FakeSession()
    service = UserService(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="positive integer"):
        await service.get_or_create_from_telegram(telegram_id=telegram_id)

    assert session.added == []
    assert session.flush_count == 0
