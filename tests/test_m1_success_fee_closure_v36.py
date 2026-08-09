from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.m1_enforcement_service import M1EnforcementService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.user import User


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm1-close-v36.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


async def seed_enforcement_case(session):
    user = User(telegram_id=983201, full_name="Клиент Закрытие")
    lawyer = Lawyer(full_name="Юрист Закрытие", is_active=True)
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number="M1-CLOSE-V36",
        client_id=user.id,
        route="M1",
        status=CaseStatus.M1_ENFORCEMENT,
        title="Финальное взыскание",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    return case, lawyer


@pytest.mark.asyncio
async def test_actual_recovery_success_fee_webhook_closes_case_end_to_end(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            case, lawyer = await seed_enforcement_case(session)
            result = await M1EnforcementService(session).record_money_received(
                case=case,
                lawyer_id=lawyer.id,
                amount="250000.00",
                comment="Исполнитель перечислил деньги клиенту",
            )
            await session.commit()

            assert case.status == CaseStatus.M1_WAITING_SUCCESS_FEE
            assert result.recovered_amount == Decimal("250000.00")
            assert result.success_fee_amount == Decimal("25000.00")
            assert result.payment.payment_code == PaymentCode.M1_SUCCESS_FEE
            assert Decimal(str(result.payment.amount)) == Decimal("25000.00")
            assert result.payment.status == PaymentStatus.PENDING

            service = PaymentWebhookService(session)
            await service.process_successful_payment(
                payment=result.payment,
                case=case,
                provider_payload={
                    "event": "payment.succeeded",
                    "provider_payment_id": "success-fee-v36",
                },
            )
            await session.commit()
            await session.refresh(case)
            await session.refresh(result.payment)

            assert result.payment.status == PaymentStatus.PAID
            assert case.status == CaseStatus.M1_CLOSED

            # A provider may retry the same successful webhook. Once PAID, the
            # handler must return without creating a second close transition,
            # notification or PAYMENT_WEBHOOK_PROCESSED audit event.
            await service.process_successful_payment(
                payment=result.payment,
                case=case,
                provider_payload={
                    "event": "payment.succeeded",
                    "provider_payment_id": "success-fee-v36",
                    "delivery": "duplicate",
                },
            )
            await session.commit()
            await session.refresh(case)
            await session.refresh(result.payment)

            assert result.payment.status == PaymentStatus.PAID
            assert case.status == CaseStatus.M1_CLOSED

            transitions = (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.entity_type == "case")
                    .where(AuditLog.entity_id == case.id)
                    .order_by(AuditLog.id.asc())
                )
            ).scalars().all()
            encoded = " ".join(
                f"{event.action} {event.old_value} {event.new_value}"
                for event in transitions
            )
            assert "M1_MONEY_RECEIVED" in encoded
            assert "M1_WAITING_SUCCESS_FEE" in encoded
            assert "M1_SUCCESS_FEE_RECEIVED" in encoded
            assert "M1_CLOSED" in encoded
            assert "PAYMENT_WEBHOOK_PROCESSED" in encoded

            close_transitions = [
                event
                for event in transitions
                if event.action == "CASE_STATUS_CHANGED"
                and (event.new_value or {}).get("status") == CaseStatus.M1_CLOSED
            ]
            webhook_events = [
                event
                for event in transitions
                if event.action == "PAYMENT_WEBHOOK_PROCESSED"
                and (event.new_value or {}).get("payment_id") == result.payment.id
            ]
            assert len(close_transitions) == 1
            assert len(webhook_events) == 1

            close_notifications = (
                await session.execute(
                    select(Notification).where(
                        Notification.case_id == case.id,
                        Notification.event_code == "M1_CLOSED",
                        Notification.recipient_type == "client",
                    )
                )
            ).scalars().all()
            assert len(close_notifications) == 1
            close_notification = close_notifications[0]
            assert close_notification.status == "PENDING"
            assert close_notification.target_chat_id == 983201
            assert "Финальный платёж" in close_notification.text
            assert "дело закрыто" in close_notification.text
            assert close_notification.dedupe_key.startswith(
                f"payment:{result.payment.id}:m1-closed:client:"
            )
