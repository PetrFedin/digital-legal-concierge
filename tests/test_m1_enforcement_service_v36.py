from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.m1_enforcement_service import M1EnforcementService
from app.domain.cases.m1_recovery_amount import MONEY_RECEIVED_AUDIT_ACTION
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
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
    user = User(telegram_id=983101, full_name="Клиент Исполнение")
    lawyer = Lawyer(full_name="Юрист Исполнение", is_active=True)
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number="M1-ENFORCEMENT-1",
        client_id=user.id,
        route="M1",
        status=CaseStatus.M1_ENFORCEMENT,
        title="Исполнительное производство",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    return case, lawyer


@pytest.mark.asyncio
async def test_money_received_opens_exact_success_fee_payment(tmp_path):
    async with database(tmp_path, "enforcement-fee.db") as factory:
        async with factory() as session:
            case, lawyer = await seed_case(session)

            result = await M1EnforcementService(session).record_money_received(
                case=case,
                lawyer_id=lawyer.id,
                amount="250000.00",
                comment="Деньги поступили клиенту по исполнительному производству",
            )

            assert result.recovered_amount == Decimal("250000.00")
            assert result.success_fee_amount == Decimal("25000.00")
            assert case.status == CaseStatus.M1_WAITING_SUCCESS_FEE
            payment = (
                await session.execute(
                    select(Payment).where(
                        Payment.case_id == case.id,
                        Payment.payment_code == PaymentCode.M1_SUCCESS_FEE,
                    )
                )
            ).scalar_one()
            assert Decimal(str(payment.amount)) == Decimal("25000.00")
            event = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == case.id,
                        AuditLog.action == MONEY_RECEIVED_AUDIT_ACTION,
                    )
                )
            ).scalar_one()
            assert event.new_value == {"amount": "250000.00", "currency": "RUB"}


@pytest.mark.asyncio
async def test_duplicate_or_foreign_money_record_is_blocked(tmp_path):
    async with database(tmp_path, "enforcement-guard.db") as factory:
        async with factory() as session:
            case, lawyer = await seed_case(session)
            service = M1EnforcementService(session)
            await service.record_money_received(
                case=case,
                lawyer_id=lawyer.id,
                amount="100000",
            )
            with pytest.raises(ValueError, match="уже зафиксирована"):
                await service.record_money_received(
                    case=case,
                    lawyer_id=lawyer.id,
                    amount="100000",
                )

            foreign = Lawyer(full_name="Другой юрист", is_active=True)
            session.add(foreign)
            await session.flush()
            second_case, _ = await seed_case(session)
            second_case.case_number = "M1-ENFORCEMENT-2"
            with pytest.raises(ValueError, match="не назначено текущему юристу"):
                await service.record_money_received(
                    case=second_case,
                    lawyer_id=foreign.id,
                    amount="100000",
                )
