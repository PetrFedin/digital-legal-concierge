from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.payment import Payment
from app.models.user import User


@pytest.fixture
async def constraints_db(tmp_path):
    database_path = tmp_path / "active-workflow-constraints.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_user(session, *, suffix: int) -> User:
    user = User(
        telegram_id=1_030_000 + suffix,
        telegram_username=f"constraint_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    session.add(user)
    await session.flush()
    return user


async def seed_case(session, *, suffix: int):
    user = await seed_user(session, suffix=suffix)
    case = Case(
        case_number=f"CONSTRAINT-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_DESCRIPTION_PENDING.value,
        title="Юридическая консультация",
    )
    session.add(case)
    await session.flush()
    return case


def payment(case_id: int, *, status: str):
    return Payment(
        case_id=case_id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=status,
    )


@pytest.mark.asyncio
async def test_database_rejects_two_active_m2_cases_for_one_client(constraints_db):
    async with constraints_db() as session:
        user = await seed_user(session, suffix=100)
        session.add_all(
            [
                Case(
                    case_number="ACTIVE-M2-ONE",
                    client_id=user.id,
                    route=RouteCode.M2.value,
                    status=CaseStatus.M2_DESCRIPTION_PENDING.value,
                    title="Первая консультация",
                ),
                Case(
                    case_number="ACTIVE-M2-TWO",
                    client_id=user.id,
                    route=RouteCode.M2.value,
                    status=CaseStatus.M2_SLOT_PENDING.value,
                    title="Вторая консультация",
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_database_allows_closed_m2_history_with_new_active_m2(constraints_db):
    async with constraints_db() as session:
        user = await seed_user(session, suffix=101)
        session.add_all(
            [
                Case(
                    case_number="CLOSED-M2-HISTORY",
                    client_id=user.id,
                    route=RouteCode.M2.value,
                    status=CaseStatus.M2_CLOSED.value,
                    title="Завершённая консультация",
                ),
                Case(
                    case_number="NEW-ACTIVE-M2",
                    client_id=user.id,
                    route=RouteCode.M2.value,
                    status=CaseStatus.M2_DESCRIPTION_PENDING.value,
                    title="Новая консультация",
                ),
            ]
        )

        await session.flush()


@pytest.mark.asyncio
async def test_database_rejects_two_active_consultations_for_one_case(constraints_db):
    async with constraints_db() as session:
        case = await seed_case(session, suffix=1)
        session.add_all(
            [
                Consultation(
                    case_id=case.id,
                    status=ConsultationStatus.DESCRIPTION_PENDING.value,
                ),
                Consultation(
                    case_id=case.id,
                    status=ConsultationStatus.SLOT_PENDING.value,
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_database_allows_active_consultation_with_closed_history(constraints_db):
    async with constraints_db() as session:
        case = await seed_case(session, suffix=2)
        session.add_all(
            [
                Consultation(
                    case_id=case.id,
                    status=ConsultationStatus.DONE.value,
                ),
                Consultation(
                    case_id=case.id,
                    status=ConsultationStatus.DESCRIPTION_PENDING.value,
                ),
            ]
        )

        await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_status,second_status",
    [
        (PaymentStatus.PENDING.value, PaymentStatus.PENDING.value),
        (
            PaymentStatus.PENDING.value,
            PaymentStatus.WAITING_CONFIRMATION.value,
        ),
        (
            PaymentStatus.WAITING_CONFIRMATION.value,
            PaymentStatus.WAITING_CONFIRMATION.value,
        ),
    ],
)
async def test_database_rejects_two_open_payments_for_same_stage(
    constraints_db,
    first_status,
    second_status,
):
    async with constraints_db() as session:
        case = await seed_case(session, suffix=10 + len(first_status + second_status))
        session.add_all(
            [
                payment(case.id, status=first_status),
                payment(case.id, status=second_status),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_database_allows_terminal_payment_history_with_new_open_payment(
    constraints_db,
):
    async with constraints_db() as session:
        case = await seed_case(session, suffix=4)
        session.add_all(
            [
                payment(case.id, status=PaymentStatus.FAILED.value),
                payment(case.id, status=PaymentStatus.PENDING.value),
            ]
        )

        await session.flush()
