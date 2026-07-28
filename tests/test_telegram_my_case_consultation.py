from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.my_case import my_case
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User


class FakeOutput:
    def __init__(self):
        self.texts: list[str] = []
        self.markups = []

    async def edit_text(self, text, **kwargs):
        self.texts.append(text)
        self.markups.append(kwargs.get("reply_markup"))


class FakeCallback:
    def __init__(self, telegram_user):
        self.from_user = telegram_user
        self.data = "my_case_open"
        self.message = FakeOutput()

    async def answer(self, *args, **kwargs):
        return None


def telegram_user(telegram_id: int):
    return SimpleNamespace(
        id=telegram_id,
        username=f"my_case_{telegram_id}",
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
async def my_case_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'my-case-consultation.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_case(
    session,
    *,
    suffix: int,
    consultation_status: str,
    case_status: str,
    slot_status: str,
    hold_delta_minutes: int | None,
    payment_status: str,
    manual_review: bool = False,
):
    now = datetime.now(timezone.utc)
    tg = telegram_user(998_000 + suffix)
    user = User(
        telegram_id=tg.id,
        telegram_username=tg.username,
        full_name=tg.full_name,
    )
    lawyer = Lawyer(
        full_name="Анна Юристова",
        phone="+7-900-INTERNAL",
        email="private@example.test",
        specialization="Споры по ДДУ",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number=f"MY-CASE-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=case_status,
        title="Юридическая консультация",
        next_action="Продолжить оформление",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=consultation_status,
        scheduled_at=now + timedelta(days=2),
        consultation_type="online",
    )
    session.add(consultation)
    await session.flush()
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=2, minutes=45),
        status=slot_status,
        hold_expires_at=(
            now + timedelta(minutes=hold_delta_minutes)
            if hold_delta_minutes is not None
            else None
        ),
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Юридическая консультация",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=payment_status,
        provider="fake",
        provider_payment_id=f"my-case-payment-{suffix}",
        payment_url="https://payments.example.test/current",
        manual_review_required=manual_review,
    )
    session.add(payment)
    await session.commit()
    return tg, case, consultation, slot, payment, lawyer


@pytest.mark.asyncio
async def test_my_case_shows_full_active_hold_without_internal_data(my_case_db):
    async with my_case_db() as session:
        tg, case, _, _, _, lawyer = await seed_case(
            session,
            suffix=1,
            consultation_status=ConsultationStatus.PAYMENT_PENDING.value,
            case_status=CaseStatus.M2_PAYMENT_PENDING.value,
            slot_status="held",
            hold_delta_minutes=15,
            payment_status=PaymentStatus.PENDING.value,
        )
        callback = FakeCallback(tg)

        await my_case(callback, session)

        text = callback.message.texts[-1]
        assert case.case_number in text
        assert "Дата и начало" in text
        assert "Окончание" in text
        assert "Продолжительность: 45 минут" in text
        assert "Москва, UTC+3" in text
        assert lawyer.full_name in text
        assert "Оплата: ожидается" in text
        assert "около" in text
        assert lawyer.phone not in text
        assert lawyer.email not in text
        assert ConsultationStatus.PAYMENT_PENDING.value not in text
        assert f"#{case.id}" not in text
        assert "next_action" in callback_values(callback.message.markups[-1])


@pytest.mark.asyncio
async def test_my_case_expired_hold_is_released_before_render(my_case_db):
    async with my_case_db() as session:
        tg, case, consultation, slot, _, _ = await seed_case(
            session,
            suffix=2,
            consultation_status=ConsultationStatus.PAYMENT_PENDING.value,
            case_status=CaseStatus.M2_PAYMENT_PENDING.value,
            slot_status="held",
            hold_delta_minutes=-1,
            payment_status=PaymentStatus.PENDING.value,
        )
        callback = FakeCallback(tg)

        await my_case(callback, session)

        await session.refresh(case)
        await session.refresh(consultation)
        await session.refresh(slot)
        text = callback.message.texts[-1]
        assert slot.status == "available"
        assert consultation.slot_id is None
        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert case.status == CaseStatus.M2_SLOT_PENDING.value
        assert "Выберите удобный способ, дату и время" in text
        assert "consult_slot_open" in callback_values(
            callback.message.markups[-1]
        ) or "next_action" in callback_values(callback.message.markups[-1])


@pytest.mark.asyncio
async def test_manual_review_tells_client_not_to_pay_again(my_case_db):
    async with my_case_db() as session:
        tg, _, _, _, _, _ = await seed_case(
            session,
            suffix=3,
            consultation_status=(
                ConsultationStatus.PAID_PENDING_CONFIRMATION.value
            ),
            case_status=CaseStatus.M2_PAYMENT_PENDING.value,
            slot_status="booked",
            hold_delta_minutes=None,
            payment_status=PaymentStatus.WAITING_CONFIRMATION.value,
            manual_review=True,
        )
        callback = FakeCallback(tg)

        await my_case(callback, session)

        text = callback.message.texts[-1]
        assert "Повторно платить не нужно" in text
        assert "Оплата: проверяется сотрудником" in text
        assert "next_action" in callback_values(callback.message.markups[-1])


@pytest.mark.asyncio
async def test_booked_consultation_links_to_details(my_case_db):
    async with my_case_db() as session:
        tg, _, _, _, _, _ = await seed_case(
            session,
            suffix=4,
            consultation_status=ConsultationStatus.BOOKED.value,
            case_status=CaseStatus.M2_CONSULTATION_BOOKED.value,
            slot_status="booked",
            hold_delta_minutes=None,
            payment_status=PaymentStatus.PAID.value,
        )
        callback = FakeCallback(tg)

        await my_case(callback, session)

        text = callback.message.texts[-1]
        values = callback_values(callback.message.markups[-1])
        assert "Статус: назначена" in text
        assert "Оплата: оплачено" in text
        assert "consultation_booked_open" in values
