from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.consultations import (
    choose_slot,
    confirm_consultation_cancel,
    consult_cancel,
    consult_reschedule_slot,
    consultation_booked_open,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.audit_log import AuditLog
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
        username=f"client_{telegram_id}",
        full_name=f"Клиент {telegram_id}",
    )


def callback_values(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


@pytest.fixture
async def telegram_management_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'telegram-management.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_booked(session, *, suffix: int = 1):
    tg = telegram_user(990_000 + suffix)
    user = User(
        telegram_id=tg.id,
        telegram_username=tg.username,
        full_name=tg.full_name,
    )
    lawyer = Lawyer(
        full_name=f"Юрист <{suffix}> & партнёры",
        specialization="Споры по ДДУ",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=f"TG-MANAGE-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        title="Юридическая консультация",
        next_action="Ожидайте консультации",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()

    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED.value,
        scheduled_at=starts_at,
        consultation_type="online",
    )
    session.add(consultation)
    await session.flush()

    old_slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    replacement = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at + timedelta(days=1),
        ends_at=starts_at + timedelta(days=1, hours=1),
        status="available",
    )
    session.add_all([old_slot, replacement])
    await session.flush()
    consultation.slot_id = old_slot.id
    await session.commit()
    return tg, user, case, consultation, old_slot, replacement


async def action_count(session, case_id: int, action: str) -> int:
    return int(
        await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
                AuditLog.action == action,
            )
        )
        or 0
    )


@pytest.mark.asyncio
async def test_booked_details_hide_raw_status_and_offer_confirmed_actions(
    telegram_management_db,
):
    async with telegram_management_db() as session:
        tg, _, _, _, _, _ = await seed_booked(session)
        callback = FakeCallback(tg, "consultation_booked_open")

        await consultation_booked_open(callback, session)

        text = callback.message.texts[-1]
        assert "Консультация назначена" in text
        assert ConsultationStatus.BOOKED.value not in text
        assert "Юрист <1> & партнёры" in text
        callbacks = callback_values(callback.message.markups[-1])
        assert "consult_reschedule" in callbacks
        assert "consult_cancel" in callbacks


@pytest.mark.asyncio
async def test_stale_initial_booking_callback_cannot_reschedule_booked_consultation(
    telegram_management_db,
):
    async with telegram_management_db() as session:
        tg, _, case, consultation, old_slot, replacement = await seed_booked(
            session,
            suffix=2,
        )
        callback = FakeCallback(
            tg,
            f"consult_slot_select:{replacement.id}",
        )

        await choose_slot(callback, session)

        await session.refresh(consultation)
        await session.refresh(old_slot)
        await session.refresh(replacement)
        assert consultation.slot_id == old_slot.id
        assert old_slot.status == "booked"
        assert replacement.status == "available"
        assert await action_count(session, case.id, "CONSULTATION_RESCHEDULED") == 0
        assert "Выбор времени сейчас недоступен" in callback.message.texts[-1]


@pytest.mark.asyncio
async def test_explicit_reschedule_callback_changes_booking_once(
    telegram_management_db,
):
    async with telegram_management_db() as session:
        tg, _, case, consultation, old_slot, replacement = await seed_booked(
            session,
            suffix=3,
        )
        callback = FakeCallback(
            tg,
            f"consult_reschedule_slot:{replacement.id}",
        )

        await consult_reschedule_slot(callback, session)

        await session.refresh(consultation)
        await session.refresh(old_slot)
        await session.refresh(replacement)
        assert consultation.slot_id == replacement.id
        assert old_slot.status == "available"
        assert replacement.status == "booked"
        assert "Время консультации изменено" in callback.message.texts[-1]
        assert await action_count(session, case.id, "CONSULTATION_RESCHEDULED") == 1

        repeated = FakeCallback(
            tg,
            f"consult_reschedule_slot:{replacement.id}",
        )
        await consult_reschedule_slot(repeated, session)
        assert await action_count(session, case.id, "CONSULTATION_RESCHEDULED") == 1


@pytest.mark.asyncio
async def test_foreign_user_cannot_reschedule_booking(telegram_management_db):
    async with telegram_management_db() as session:
        _, _, case, consultation, old_slot, replacement = await seed_booked(
            session,
            suffix=4,
        )
        foreign = telegram_user(999_999)
        callback = FakeCallback(
            foreign,
            f"consult_reschedule_slot:{replacement.id}",
        )

        await consult_reschedule_slot(callback, session)

        await session.refresh(consultation)
        await session.refresh(old_slot)
        await session.refresh(replacement)
        assert consultation.slot_id == old_slot.id
        assert old_slot.status == "booked"
        assert replacement.status == "available"
        assert await action_count(session, case.id, "CONSULTATION_RESCHEDULED") == 0
        assert "Перенос недоступен" in callback.message.texts[-1]


@pytest.mark.asyncio
async def test_cancellation_requires_confirmation_and_repeated_button_is_safe(
    telegram_management_db,
):
    async with telegram_management_db() as session:
        tg, _, case, consultation, old_slot, _ = await seed_booked(
            session,
            suffix=5,
        )
        preview = FakeCallback(tg, "consult_cancel")

        await consult_cancel(preview, session)

        await session.refresh(consultation)
        await session.refresh(old_slot)
        assert consultation.status == ConsultationStatus.BOOKED.value
        assert old_slot.status == "booked"
        assert "Вы действительно хотите" in preview.message.texts[-1]
        assert "consult_cancel_confirm" in callback_values(
            preview.message.markups[-1]
        )

        confirmed = FakeCallback(tg, "consult_cancel_confirm")
        await confirm_consultation_cancel(confirmed, session)
        await session.refresh(consultation)
        await session.refresh(old_slot)
        await session.refresh(case)
        assert consultation.status == ConsultationStatus.CANCELLED.value
        assert consultation.slot_id is None
        assert old_slot.status == "available"
        assert case.status == CaseStatus.M2_CLOSED.value
        assert await action_count(session, case.id, "CONSULTATION_CANCELLED") == 1

        repeated = FakeCallback(tg, "consult_cancel_confirm")
        await confirm_consultation_cancel(repeated, session)
        assert "уже отменена" in repeated.message.texts[-1]
        assert await action_count(session, case.id, "CONSULTATION_CANCELLED") == 1
