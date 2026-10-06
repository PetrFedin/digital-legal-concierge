from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.enforcement_service import EnforcementError, EnforcementService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.models import Base
from app.models.case import Case
from app.models.user import User


async def database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def create_enforcement_case(session, suffix: int = 1) -> Case:
    user = User(
        telegram_id=996000000 + suffix,
        full_name=f"Enforcement Client {suffix}",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"M1-ENF-{suffix}",
        client_id=user.id,
        route="M1",
        status=CaseStatus.M1_ENFORCEMENT,
        title="Исполнительное производство",
    )
    session.add(case)
    await session.flush()
    return case


@pytest.mark.asyncio
async def test_partial_and_final_receipts_accumulate_and_open_success_fee(tmp_path):
    engine, factory = await database(tmp_path, "m1-enforcement.db")
    async with factory() as session:
        case = await create_enforcement_case(session)

        service = EnforcementService(session)
        await service.update_execution(
            case=case,
            enforcement_number="12345/26/77001-ИП",
            enforcement_status="STARTED",
            actor_type="lawyer",
            actor_id=11,
        )
        await service.record_receipt(
            case=case,
            amount="15000.50",
            final=False,
            actor_type="lawyer",
            actor_id=11,
            comment="Частичное поступление по исполнительному производству",
        )
        assert case.status == CaseStatus.M1_ENFORCEMENT
        assert case.received_amount == Decimal("15000.50")
        assert case.received_at is None

        await service.record_receipt(
            case=case,
            amount="35000.00",
            final=True,
            actor_type="lawyer",
            actor_id=11,
            comment="Окончательное поступление подтверждено",
        )
        assert case.status == CaseStatus.M1_MONEY_RECEIVED
        assert case.received_amount == Decimal("50000.50")
        assert case.received_at is not None
        assert case.enforcement_status == "MONEY_RECEIVED"

        payment_service = PaymentService(session)
        amount = await payment_service.estimate_success_fee_for_case(case.id)
        assert amount == Decimal("5000.05")

        payment = await payment_service.get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M1_SUCCESS_FEE,
        )
        assert payment.amount == Decimal("5000.05")
        assert case.success_fee_amount == Decimal("5000.05")
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_success_fee_fails_closed_without_actual_receipt_amount(tmp_path):
    engine, factory = await database(tmp_path, "m1-no-receipt.db")
    async with factory() as session:
        case = await create_enforcement_case(session, suffix=2)
        case.status = CaseStatus.M1_MONEY_RECEIVED
        case.received_amount = None
        await session.flush()

        with pytest.raises(ValueError, match="фактической суммы поступления"):
            await PaymentService(session).estimate_success_fee_for_case(case.id)

    await engine.dispose()


@pytest.mark.asyncio
async def test_receipt_rejects_invalid_amount_and_duplicate_final_event(tmp_path):
    engine, factory = await database(tmp_path, "m1-receipt-guard.db")
    async with factory() as session:
        case = await create_enforcement_case(session, suffix=3)
        service = EnforcementService(session)

        with pytest.raises(EnforcementError, match="больше нуля"):
            await service.record_receipt(
                case=case,
                amount="0",
                final=False,
                actor_type="admin",
                actor_id=7,
            )

        await service.record_receipt(
            case=case,
            amount="100000",
            final=True,
            actor_type="admin",
            actor_id=7,
        )
        assert case.status == CaseStatus.M1_MONEY_RECEIVED

        with pytest.raises(EnforcementError, match="только на этапе"):
            await service.record_receipt(
                case=case,
                amount="1000",
                final=True,
                actor_type="admin",
                actor_id=7,
            )
        assert case.received_amount == Decimal("100000.00")

    await engine.dispose()
