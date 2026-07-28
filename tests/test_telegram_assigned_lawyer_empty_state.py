from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.consultation_selection import open_selection_modes
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
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
    def __init__(self, telegram_id: int):
        self.from_user = SimpleNamespace(
            id=telegram_id,
            username=f"assigned_{telegram_id}",
            full_name=f"Клиент {telegram_id}",
        )
        self.data = "consult_slot_open"
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
async def assigned_empty_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'assigned-empty.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_assigned_lawyer_without_slots_is_not_silently_replaced(
    assigned_empty_db,
):
    async with assigned_empty_db() as session:
        user = User(
            telegram_id=999_901,
            full_name="Клиент назначенного юриста",
        )
        assigned = Lawyer(
            full_name="Назначенный юрист",
            specialization="Споры по ДДУ",
            is_active=True,
            workload_limit=1,
        )
        alternative = Lawyer(
            full_name="Другой свободный юрист",
            specialization="Недвижимость",
            is_active=True,
            workload_limit=30,
        )
        session.add_all([user, assigned, alternative])
        await session.flush()
        case = Case(
            case_number="ASSIGNED-NO-SLOTS",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_SLOT_PENDING.value,
            title="Юридическая консультация",
            next_action="Выберите время",
            assigned_lawyer_id=assigned.id,
        )
        session.add(case)
        await session.flush()
        session.add(
            Consultation(
                case_id=case.id,
                status=ConsultationStatus.SLOT_PENDING.value,
                consultation_type="online",
            )
        )
        await session.commit()

        callback = FakeCallback(user.telegram_id)
        await open_selection_modes(callback, session)

        await session.refresh(case)
        text = callback.message.texts[-1]
        values = callback_values(callback.message.markups[-1])
        assert "У назначенного по делу юриста пока нет свободного времени" in text
        assert "скрытого переназначения не произошло" in text
        assert "contact_lawyer" in values
        assert "consult_select:mode:lawyer" not in values
        assert case.assigned_lawyer_id == assigned.id
