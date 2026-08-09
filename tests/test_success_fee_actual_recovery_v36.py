from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.m1_recovery_amount import MONEY_RECEIVED_AUDIT_ACTION
from app.domain.payments.payment_service import PaymentService
from app.models import Base
from app.models.case import Case
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
    user = User(telegram_id=983001, full_name="Клиент Взыскание")
    session.add(user)
    await session.flush()
    case = Case(
        case_number="M1-RECOVERY-1",
        client_id=user.id,
        route="M1",
        status="M1_MONEY_RECEIVED",
        title="Фактическое взыскание",
    )
    session.add(case)
    await session.flush()
    return case


@pytest.mark.asyncio
async def test_success_fee_is_percent_of_actual_recovered_amount(tmp_path):
    async with database(tmp_path, "actual-fee.db") as factory:
        async with factory() as session:
            case = await seed_case(session)
            await add_case_history_event(
                session,
                actor_type="lawyer",
                actor_id=1,
                case_id=case.id,
                action=MONEY_RECEIVED_AUDIT_ACTION,
                new_value={"amount": "123456.78", "currency": "RUB"},
            )
            fee = await PaymentService(session).estimate_success_fee_for_case(case.id)

            assert fee == Decimal("12345.68")


@pytest.mark.asyncio
async def test_success_fee_fails_closed_without_actual_recovery_event(tmp_path):
    async with database(tmp_path, "missing-actual-fee.db") as factory:
        async with factory() as session:
            case = await seed_case(session)

            with pytest.raises(ValueError, match="Фактически взысканная сумма не зафиксирована"):
                await PaymentService(session).estimate_success_fee_for_case(case.id)
