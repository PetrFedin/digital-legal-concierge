from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.consultation_selection import (
    choose_date,
    choose_dates,
    choose_lawyer,
    choose_mode,
    open_selection_modes,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


class FakeOutput:
    def __init__(self):
        self.texts: list[str] = []
        self.markups = []

    async def edit_text(self, text, **kwargs):
        self.texts.append(text)
        self.markups.append(kwargs.get("reply_markup"))


class FakeCallback:
    def __init__(self, telegram_user, data: str):
        self.from_user = telegram_user
        self.data = data
        self.message = FakeOutput()
        self.alerts: list[tuple[str, bool]] = []

    async def answer(self, text, show_alert=False, **kwargs):
        self.alerts.append((text, show_alert))


def telegram_user(telegram_id: int):
    return SimpleNamespace(
        id=telegram_id,
        username=f"selection_{telegram_id}",
        full_name=f"Клиент {telegram_id}",
    )


def buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def callbacks(markup) -> list[str]:
    return [button.callback_data for button in buttons(markup) if button.callback_data]


@pytest.fixture
async def telegram_selection_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'telegram-selection.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_selection(session, *, suffix: int = 1, assigned: bool = False):
    now = datetime.now(timezone.utc)
    tg = telegram_user(997_000 + suffix)
    user = User(
        telegram_id=tg.id,
        telegram_username=tg.username,
        full_name=tg.full_name,
    )
    lawyer = Lawyer(
        full_name="Анна <Петрова> & партнёры",
        phone="+7-900-INTERNAL",
        email="internal-lawyer@example.test",
        specialization="Споры по ДДУ",
        is_active=True,
        workload_limit=10,
    )
    second_lawyer = Lawyer(
        full_name="Иван Смирнов",
        specialization="Недвижимость",
        is_active=True,
        workload_limit=10,
    )
    session.add_all([user, lawyer, second_lawyer])
    await session.flush()

    case = Case(
        case_number=f"TG-SELECTION-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_SLOT_PENDING.value,
        title="Юридическая консультация",
        next_action="Выберите время",
        assigned_lawyer_id=lawyer.id if assigned else None,
    )
    session.add(case)
    await session.flush()
    consultation = Consultation(
        case_id=case.id,
        status=ConsultationStatus.SLOT_PENDING.value,
        consultation_type="online",
    )
    session.add(consultation)
    await session.flush()

    slots = []
    for offset in range(1, 10):
        slot = ConsultationSlot(
            lawyer_id=lawyer.id if offset % 2 else second_lawyer.id,
            starts_at=now + timedelta(days=offset, hours=2),
            ends_at=now + timedelta(days=offset, hours=2, minutes=45),
            status="available",
        )
        session.add(slot)
        slots.append(slot)
    await session.commit()
    return tg, user, case, consultation, lawyer, second_lawyer, slots


@pytest.mark.asyncio
async def test_entry_shows_three_modes_without_assigned_lawyer(
    telegram_selection_db,
):
    async with telegram_selection_db() as session:
        tg, _, _, _, _, _, _ = await seed_selection(session)
        callback = FakeCallback(tg, "consult_slot_open")

        await open_selection_modes(callback, session)

        values = callbacks(callback.message.markups[-1])
        assert "Как вам удобнее" in callback.message.texts[-1]
        assert "consult_select:mode:nearest" in values
        assert "consult_select:mode:lawyer" in values
        assert "consult_select:dates:0:0" in values
        assert "consult_select:mode:assigned" not in values


@pytest.mark.asyncio
async def test_assigned_lawyer_mode_is_shown_only_with_own_future_slots(
    telegram_selection_db,
):
    async with telegram_selection_db() as session:
        tg, _, _, _, lawyer, _, _ = await seed_selection(
            session,
            suffix=2,
            assigned=True,
        )
        callback = FakeCallback(tg, "consult_slot_open")

        await open_selection_modes(callback, session)

        values = callbacks(callback.message.markups[-1])
        assert "consult_select:mode:assigned" in values
        assigned = FakeCallback(tg, "consult_select:mode:assigned")
        await choose_mode(assigned, session)
        assert lawyer.full_name in assigned.message.texts[-1]
        assert "workload" not in assigned.message.texts[-1].lower()
        assert lawyer.phone not in assigned.message.texts[-1]
        assert lawyer.email not in assigned.message.texts[-1]


@pytest.mark.asyncio
async def test_nearest_and_lawyer_cards_are_client_safe(telegram_selection_db):
    async with telegram_selection_db() as session:
        tg, _, _, _, lawyer, _, _ = await seed_selection(session, suffix=3)
        nearest = FakeCallback(tg, "consult_select:mode:nearest")
        await choose_mode(nearest, session)

        text = nearest.message.texts[-1]
        assert lawyer.full_name in text
        assert lawyer.specialization in text
        assert lawyer.phone not in text
        assert lawyer.email not in text
        assert "workload_limit" not in text
        assert "available_capacity" not in text
        assert all(len(value.encode("utf-8")) <= 64 for value in callbacks(nearest.message.markups[-1]))

        lawyer_list = FakeCallback(tg, "consult_select:mode:lawyer")
        await choose_mode(lawyer_list, session)
        lawyer_callback = next(
            value
            for value in callbacks(lawyer_list.message.markups[-1])
            if value.startswith("consult_select:lawyer:")
        )
        card = FakeCallback(tg, lawyer_callback)
        await choose_lawyer(card, session)
        assert lawyer.full_name in card.message.texts[-1]
        assert lawyer.phone not in card.message.texts[-1]
        assert lawyer.email not in card.message.texts[-1]


@pytest.mark.asyncio
async def test_date_pagination_and_final_slot_callback(telegram_selection_db):
    async with telegram_selection_db() as session:
        tg, _, _, _, _, _, _ = await seed_selection(session, suffix=4)
        first_page = FakeCallback(tg, "consult_select:dates:0:0")
        await choose_dates(first_page, session)
        first_values = callbacks(first_page.message.markups[-1])
        assert any(value.startswith("consult_select:date:") for value in first_values)
        assert "consult_select:dates:1:0" in first_values

        second_page = FakeCallback(tg, "consult_select:dates:1:0")
        await choose_dates(second_page, session)
        second_values = callbacks(second_page.message.markups[-1])
        date_callback = next(
            value
            for value in second_values
            if value.startswith("consult_select:date:")
        )
        date_screen = FakeCallback(tg, date_callback)
        await choose_date(date_screen, session)
        slot_values = callbacks(date_screen.message.markups[-1])
        assert any(value.startswith("consult_slot_select:") for value in slot_values)
        assert all(len(value.encode("utf-8")) <= 64 for value in slot_values)

        out_of_range = FakeCallback(tg, "consult_select:dates:99:0")
        await choose_dates(out_of_range, session)
        out_values = callbacks(out_of_range.message.markups[-1])
        assert "consult_select:dates:0:0" in out_values
        assert not any(value == "consult_select:dates:100:0" for value in out_values)


@pytest.mark.asyncio
async def test_stale_and_foreign_selection_callbacks_are_rejected(
    telegram_selection_db,
):
    async with telegram_selection_db() as session:
        tg, _, case, _, _, _, _ = await seed_selection(session, suffix=5)
        case.status = CaseStatus.M2_PAYMENT_PENDING.value
        await session.commit()
        stale = FakeCallback(tg, "consult_select:mode:nearest")
        await choose_mode(stale, session)
        assert "Расписание больше недоступно" in stale.message.texts[-1]

        foreign = FakeCallback(
            telegram_user(997_999),
            "consult_select:mode:nearest",
        )
        await choose_mode(foreign, session)
        assert "Расписание больше недоступно" in foreign.message.texts[-1]
