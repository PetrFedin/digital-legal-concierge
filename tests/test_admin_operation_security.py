from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.admin as admin_api
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, create_access_token


async def create_database(tmp_path):
    database_path = tmp_path / "admin-operation-security.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def create_fake_payment(session):
    user = User(
        telegram_id=980001,
        full_name="Клиент тестовой оплаты",
    )
    session.add(user)
    await session.flush()

    case = Case(
        case_number="ADMIN-PAY-001",
        client_id=user.id,
        route="M1",
        status="M1_WAITING_PAYMENT_30000",
        title="Тест защищённого подтверждения",
    )
    session.add(case)
    await session.flush()

    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M1_INITIAL_PAYMENT,
        title="Первый платёж М1",
        amount=Decimal("30000.00"),
        currency="RUB",
        status=PaymentStatus.WAITING_CONFIRMATION,
        provider="fake",
        provider_payment_id="fake-admin-confirm",
    )
    session.add(payment)
    await session.flush()
    return case, payment


def test_manual_confirmation_is_disabled_for_production_yookassa(monkeypatch):
    monkeypatch.setattr(
        admin_api,
        "settings",
        SimpleNamespace(
            payment_provider="yookassa",
            app_env="production",
            demo_mode=False,
        ),
    )
    assert admin_api.manual_payment_confirmation_enabled() is False


@pytest.mark.asyncio
async def test_production_manual_confirmation_fails_before_database_access(monkeypatch):
    monkeypatch.setattr(
        admin_api,
        "settings",
        SimpleNamespace(
            payment_provider="yookassa",
            app_env="production",
            demo_mode=False,
        ),
    )
    token = create_access_token(
        user_id=701,
        username="production-admin",
        roles=[ROLE_ADMIN],
    )
    with pytest.raises(HTTPException) as error:
        await admin_api.manual_confirm_payment(
            payment_id=1,
            db=None,
            x_admin_token=token,
        )
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_fake_manual_confirmation_records_real_admin_actor(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        admin_api,
        "settings",
        SimpleNamespace(
            payment_provider="fake",
            app_env="test",
            demo_mode=False,
        ),
    )
    engine, session_factory = await create_database(tmp_path)
    async with session_factory() as session:
        case, payment = await create_fake_payment(session)
        await session.commit()

        real_admin_id = 702
        token = create_access_token(
            user_id=real_admin_id,
            username="test-payment-admin",
            roles=[ROLE_ADMIN],
        )
        response = await admin_api.manual_confirm_payment(
            payment_id=payment.id,
            db=session,
            x_admin_token=token,
        )

        assert response["ok"] is True
        await session.refresh(payment)
        assert payment.status == PaymentStatus.PAID

        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "ADMIN_FAKE_PAYMENT_CONFIRMED",
                )
            )
        ).scalar_one()
        assert audit.actor_type == "admin"
        assert audit.actor_id == real_admin_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_already_paid_payment_cannot_be_manually_reconfirmed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        admin_api,
        "settings",
        SimpleNamespace(
            payment_provider="fake",
            app_env="test",
            demo_mode=False,
        ),
    )
    engine, session_factory = await create_database(tmp_path)
    async with session_factory() as session:
        _case, payment = await create_fake_payment(session)
        payment.status = PaymentStatus.PAID
        await session.commit()

        token = create_access_token(
            user_id=703,
            username="test-payment-admin-2",
            roles=[ROLE_ADMIN],
        )
        with pytest.raises(HTTPException) as error:
            await admin_api.manual_confirm_payment(
                payment_id=payment.id,
                db=session,
                x_admin_token=token,
            )
        assert error.value.status_code == 409

    await engine.dispose()
