from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.m1_recovery_amount import MONEY_RECEIVED_AUDIT_ACTION
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User


@asynccontextmanager
async def database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


async def seed_case(session):
    user = User(telegram_id=983401, full_name="Клиент Success Fee")
    session.add(user)
    await session.flush()
    case = Case(
        case_number="M1-FEE-REUSE-V36",
        client_id=user.id,
        route="M1",
        status=CaseStatus.M1_WAITING_SUCCESS_FEE,
        title="Финальный платёж",
    )
    session.add(case)
    await session.flush()
    await add_case_history_event(
        session,
        actor_type="lawyer",
        actor_id=1,
        case_id=case.id,
        action=MONEY_RECEIVED_AUDIT_ACTION,
        new_value={"amount": "250000.00", "currency": "RUB"},
    )
    return case


def payment(case_id: int, *, amount: str, status: PaymentStatus) -> Payment:
    return Payment(
        case_id=case_id,
        payment_code=PaymentCode.M1_SUCCESS_FEE,
        title="Success fee",
        amount=Decimal(amount),
        currency="RUB",
        status=status,
    )


@pytest.mark.asyncio
async def test_failed_historical_fee_is_not_reused_and_correct_pending_is_created(tmp_path):
    async with database(tmp_path, "historical-fee.db") as factory:
        async with factory() as session:
            case = await seed_case(session)
            historical = payment(
                case.id,
                amount="9999.00",
                status=PaymentStatus.FAILED,
            )
            session.add(historical)
            await session.flush()

            service = PaymentService(session)
            expected = await service.estimate_success_fee_for_case(case.id)
            current = await service.get_or_create_payment(
                case=case,
                payment_code=PaymentCode.M1_SUCCESS_FEE,
                amount=expected,
            )

            assert expected == Decimal("25000.00")
            assert current.id != historical.id
            assert Decimal(str(current.amount)) == Decimal("25000.00")
            assert current.status == PaymentStatus.PENDING
            rows = (
                await session.execute(
                    select(Payment).where(
                        Payment.case_id == case.id,
                        Payment.payment_code == PaymentCode.M1_SUCCESS_FEE,
                    )
                )
            ).scalars().all()
            assert {item.status for item in rows} == {
                PaymentStatus.FAILED,
                PaymentStatus.PENDING,
            }


@pytest.mark.asyncio
async def test_matching_pending_success_fee_is_reused_without_duplicate(tmp_path):
    async with database(tmp_path, "matching-fee.db") as factory:
        async with factory() as session:
            case = await seed_case(session)
            existing = payment(
                case.id,
                amount="25000.00",
                status=PaymentStatus.PENDING,
            )
            session.add(existing)
            await session.flush()

            current = await PaymentService(session).get_or_create_payment(
                case=case,
                payment_code=PaymentCode.M1_SUCCESS_FEE,
                amount=Decimal("25000.00"),
            )

            assert current.id == existing.id
            count = len(
                (
                    await session.execute(
                        select(Payment).where(
                            Payment.case_id == case.id,
                            Payment.payment_code == PaymentCode.M1_SUCCESS_FEE,
                        )
                    )
                ).scalars().all()
            )
            assert count == 1


@pytest.mark.asyncio
async def test_wrong_waiting_confirmation_fee_fails_closed(tmp_path):
    async with database(tmp_path, "wrong-active-fee.db") as factory:
        async with factory() as session:
            case = await seed_case(session)
            existing = payment(
                case.id,
                amount="9999.00",
                status=PaymentStatus.WAITING_CONFIRMATION,
            )
            session.add(existing)
            await session.flush()

            with pytest.raises(ValueError, match="другую сумму"):
                await PaymentService(session).get_or_create_payment(
                    case=case,
                    payment_code=PaymentCode.M1_SUCCESS_FEE,
                    amount=Decimal("25000.00"),
                )
