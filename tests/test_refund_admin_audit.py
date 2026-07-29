from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.refund_center import require_admin, resolve_refund
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, create_access_token


async def create_database(tmp_path):
    database_path = tmp_path / "refund-admin-audit.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def create_refund_payment(session):
    user = User(
        telegram_id=970001,
        full_name="Клиент проверки аудита",
    )
    session.add(user)
    await session.flush()

    case = Case(
        case_number="REFUND-ACTOR-001",
        client_id=user.id,
        route="M2",
        status="M2_CLOSED",
        title="Проверка автора возврата",
    )
    session.add(case)
    await session.flush()

    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.REFUND_PENDING,
        provider="fake",
        provider_payment_id="refund-actor-test",
    )
    session.add(payment)
    await session.flush()
    return case, payment


@pytest.mark.asyncio
async def test_refund_resolution_uses_actor_from_signed_token(tmp_path):
    engine, session_factory = await create_database(tmp_path)
    async with session_factory() as session:
        case, payment = await create_refund_payment(session)
        await session.commit()

        real_admin_id = 321
        token = create_access_token(
            user_id=real_admin_id,
            username="refund-admin",
            roles=[ROLE_ADMIN],
        )
        response = await resolve_refund(
            payment_id=payment.id,
            payload={
                "decision": "refunded",
                "comment": "Возврат выполнен, операция R-AUDIT-001",
                "actor_id": 999999,
            },
            db=session,
            x_admin_token=token,
        )

        assert response["ok"] is True
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "CONSULTATION_REFUND_COMPLETED",
                )
            )
        ).scalar_one()
        assert audit.actor_type == "admin"
        assert audit.actor_id == real_admin_id
        assert audit.actor_id != 999999

    await engine.dispose()


def test_lawyer_token_cannot_access_refund_admin_api():
    token = create_access_token(
        user_id=654,
        username="lawyer-only",
        roles=[ROLE_LAWYER],
    )
    with pytest.raises(HTTPException) as error:
        require_admin(token)
    assert error.value.status_code == 403
