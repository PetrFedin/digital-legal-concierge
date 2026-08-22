from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.bot.screens import common, my_case, payments
from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models.user import User


def _telegram_id() -> int:
    return 7_300_000_000_000 + (uuid.uuid4().int % 1_000_000_000)


def _from_user(telegram_id: int):
    return SimpleNamespace(
        id=telegram_id,
        username=f"multi_nav_{telegram_id}",
        full_name="Multi Case Navigation",
    )


class _FakeMessage:
    def __init__(self) -> None:
        self.text: str | None = None
        self.reply_markup = None

    async def edit_text(self, text: str, *, reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup
        return self

    async def answer(self, text: str, *, reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup
        return self


class _FakeCallback:
    def __init__(self, telegram_id: int) -> None:
        self.from_user = _from_user(telegram_id)
        self.message = _FakeMessage()

    async def answer(self, *args, **kwargs):
        return None


async def _seed_ambiguous_context(db, telegram_id: int):
    user = User(
        telegram_id=telegram_id,
        telegram_username=f"multi_nav_{telegram_id}",
        full_name="Multi Case Navigation",
    )
    db.add(user)
    await db.flush()
    service = CaseService(db)

    async def create(status, *, route=None):
        return await service.create_case_for_operation(
            client=user,
            operation_key=f"pytest:multi-nav:{uuid.uuid4().hex}",
            purpose="multi_case_navigation_test",
            route=route,
            status=status,
            title="Multi case navigation test",
        )

    first = await create(CaseStatus.CALCULATOR_STARTED)
    second = await create(CaseStatus.CALCULATOR_STARTED)
    selected = await create(
        CaseStatus.M1_SUCCESS_FEE_RECEIVED,
        route=RouteCode.M1.value,
    )
    await service.change_status(
        case=selected,
        next_status=CaseStatus.M1_CLOSED,
        actor_type="system",
        actor_id=None,
        comment="Selected Case closed while two other matters stay active",
    )
    await db.commit()
    return user, first, second, selected


def _callbacks(markup) -> list[str]:
    return [
        str(button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


@pytest.mark.asyncio
async def test_home_shows_explicit_selector_instead_of_completed_archive_on_ambiguity():
    telegram_id = _telegram_id()
    async with AsyncSessionLocal() as db:
        _user, first, second, selected = await _seed_ambiguous_context(
            db,
            telegram_id,
        )
        message = SimpleNamespace(from_user=_from_user(telegram_id))

        text, case_exists, completed_case, primary = await common._home_text(
            db,
            message,
        )

        assert case_exists is True
        assert completed_case is False
        assert primary == common.CASE_SELECTION_ACTION
        assert "Выберите обращение" in text
        assert "Активных обращений: 2" in text
        assert "Последнее дело завершено" not in text
        assert str(selected.case_number) not in text
        assert int(first.id) != int(second.id)


@pytest.mark.asyncio
async def test_my_case_renders_only_active_selector_when_selected_matter_closed():
    telegram_id = _telegram_id()
    async with AsyncSessionLocal() as db:
        _user, first, second, selected = await _seed_ambiguous_context(
            db,
            telegram_id,
        )
        first_number = str(first.case_number)
        second_number = str(second.case_number)
        selected_number = str(selected.case_number)
        callback = _FakeCallback(telegram_id)

        await my_case._render_case(callback, db)

        assert callback.message.text is not None
        assert "📁 МОИ ОБРАЩЕНИЯ" in callback.message.text
        assert first_number in callback.message.text
        assert second_number in callback.message.text
        assert selected_number not in callback.message.text
        assert "ИТОГ ДЕЛА" not in callback.message.text
        callbacks = _callbacks(callback.message.reply_markup)
        assert f"my_case_select:v2:{int(first.id)}" in callbacks
        assert f"my_case_select:v2:{int(second.id)}" in callbacks
        assert "calc_start" in callbacks


@pytest.mark.asyncio
async def test_payment_cabinet_requires_case_selection_before_showing_financial_history():
    telegram_id = _telegram_id()
    async with AsyncSessionLocal() as db:
        await _seed_ambiguous_context(db, telegram_id)
        callback = _FakeCallback(telegram_id)

        await payments.payments(callback, db)

        assert callback.message.text is not None
        assert "несколько активных обращений" in callback.message.text
        assert "не открываются без точного контекста" in callback.message.text
        assert "завершённого дела" not in callback.message.text
        assert _callbacks(callback.message.reply_markup) == [
            "my_cases_open",
            "nav_home",
        ]


def test_fake_payment_confirmation_is_never_enabled_by_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "fake")
    monkeypatch.setattr(settings, "demo_mode", True)

    monkeypatch.setattr(settings, "app_env", "production")
    assert payments.fake_payments_enabled() is False

    monkeypatch.setattr(settings, "app_env", "test")
    assert payments.fake_payments_enabled() is True

    monkeypatch.setattr(settings, "payment_provider", "yookassa")
    assert payments.fake_payments_enabled() is False
