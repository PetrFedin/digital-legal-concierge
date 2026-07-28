from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.consultation_selection import choose_mode, open_selection_modes
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
    def __init__(self, telegram_id: int, data: str):
        self.from_user = SimpleNamespace(
            id=telegram_id,
            username=f"cleanup_{telegram_id}",
            full_name=f"Клиент {telegram_id}",
        )
        self.data = data
        self.message = FakeOutput()
        self.alerts = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.alerts.append((text, show_alert))


def callback_values(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


@pytest.fixture
async def cleanup_selection_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'selection-cleanup.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_hold_is_released_before_selection_render(
    cleanup_selection_db,
):
    async with cleanup_selection_db() as session:
        now = datetime.now(timezone.utc)
        current_user = User(
            telegram_id=999_801,
            full_name="Клиент выбора",
        )
        former_user = User(
            telegram_id=999_802,
            full_name="Клиент истёкшего резерва",
        )
        lawyer = Lawyer(
            full_name="Юрист освободившегося времени",
            specialization="Споры по ДДУ",
            is_active=True,
            workload_limit=10,
        )
        session.add_all([current_user, former_user, lawyer])
        await session.flush()

        current_case = Case(
            case_number="SELECTION-CURRENT",
            client_id=current_user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_SLOT_PENDING.value,
            title="Юридическая консультация",
            next_action="Выберите время",
        )
        former_case = Case(
            case_number="SELECTION-EXPIRED",
            client_id=former_user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_PAYMENT_PENDING.value,
            title="Юридическая консультация",
            next_action="Оплатите консультацию",
        )
        session.add_all([current_case, former_case])
        await session.flush()

        current_consultation = Consultation(
            case_id=current_case.id,
            status=ConsultationStatus.SLOT_PENDING.value,
            consultation_type="online",
        )
        former_consultation = Consultation(
            case_id=former_case.id,
            lawyer_id=lawyer.id,
            status=ConsultationStatus.PAYMENT_PENDING.value,
            scheduled_at=now + timedelta(days=2),
        )
        session.add_all([current_consultation, former_consultation])
        await session.flush()

        expired_slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, minutes=45),
            status="held",
            hold_expires_at=now - timedelta(minutes=1),
            held_by_user_id=former_user.id,
            consultation_id=former_consultation.id,
        )
        session.add(expired_slot)
        await session.flush()
        former_consultation.slot_id = expired_slot.id
        await session.commit()

        entry = FakeCallback(current_user.telegram_id, "consult_slot_open")
        await open_selection_modes(entry, session)

        await session.refresh(expired_slot)
        await session.refresh(former_consultation)
        await session.refresh(former_case)
        assert expired_slot.status == "available"
        assert expired_slot.consultation_id is None
        assert former_consultation.slot_id is None
        assert former_consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert former_case.status == CaseStatus.M2_SLOT_PENDING.value
        assert "consult_select:mode:nearest" in callback_values(
            entry.message.markups[-1]
        )

        nearest = FakeCallback(
            current_user.telegram_id,
            "consult_select:mode:nearest",
        )
        await choose_mode(nearest, session)
        values = callback_values(nearest.message.markups[-1])
        assert f"consult_slot_select:{expired_slot.id}" in values
        assert lawyer.full_name in nearest.message.texts[-1]
