from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_service import CaseAssignmentError, CaseService
from app.domain.payments.payment_processing_outcomes import PaymentProcessingOutcome
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User


async def _create_test_database(tmp_path, name: str):
    database_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def _count_action(session, *, case_id: int, action: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == case_id,
                    AuditLog.action == action,
                )
            )
        ).scalar_one()
    )


async def _seed_payment_conflict(session):
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    user = User(
        telegram_id=920001,
        telegram_username="admin_recovery_client",
        full_name="Клиент восстановления платежа",
    )
    lawyer = Lawyer(
        full_name="Юрист восстановления платежа",
        email="admin-recovery-lawyer@example.test",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number="TEST-ADMIN-PAYMENT-RECOVERY",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Восстановление оплаты консультации",
        next_action="Оплатить консультацию",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        slot_id=None,
        status=ConsultationStatus.PAYMENT_PENDING.value,
        scheduled_at=starts_at,
        client_description="Проверка ручного восстановления платежа.",
    )
    session.add(consultation)
    await session.flush()

    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="held",
        hold_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)

    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.WAITING_CONFIRMATION,
        provider="fake",
        provider_payment_id="provider-admin-recovery",
        payment_url="https://payments.example.test/admin-recovery",
    )
    session.add(payment)
    await session.commit()
    return case.id, consultation.id, slot.id, payment.id


@pytest.mark.asyncio
async def test_admin_reprocess_recovers_payment_after_data_fix(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "admin-payment-recovery.db",
    )

    async with session_factory() as session:
        case_id, consultation_id, slot_id, payment_id = await _seed_payment_conflict(
            session
        )

    async with session_factory() as session:
        case = await session.get(Case, case_id)
        consultation = await session.get(Consultation, consultation_id)
        slot = await session.get(ConsultationSlot, slot_id)
        payment = await session.get(Payment, payment_id)
        webhook = PaymentWebhookService(session)

        await webhook.process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"event": "payment.succeeded"},
        )
        await session.commit()

        assert payment.status == PaymentStatus.PAID
        assert payment.processing_outcome == PaymentProcessingOutcome.CONFLICT
        assert payment.manual_review_required is True
        assert payment.processed_at is None
        assert "не связана со слотом" in payment.processing_error
        assert slot.status == "held"
        assert await _count_action(
            session,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_REQUIRES_MANUAL_REVIEW",
        ) == 1

        await webhook.process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"event": "payment.succeeded.retry"},
        )
        await session.commit()

        assert payment.processing_outcome == PaymentProcessingOutcome.CONFLICT
        assert await _count_action(
            session,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_REQUIRES_MANUAL_REVIEW",
        ) == 1
        assert await _count_action(
            session,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_PROCESSED",
        ) == 0

        consultation.slot_id = slot.id
        await session.flush()

        await webhook.process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={
                "source": "admin_manual_confirm",
                "admin_user_id": 77,
            },
            allow_reprocess=True,
            actor_type="admin",
            actor_id=77,
            source="admin_manual_confirm",
        )
        await session.commit()

        assert payment.status == PaymentStatus.PAID
        assert payment.processing_outcome == PaymentProcessingOutcome.PROCESSED
        assert payment.manual_review_required is False
        assert payment.processing_error is None
        assert payment.processed_at is not None
        assert consultation.status == ConsultationStatus.PAID_PENDING_CONFIRMATION.value
        assert slot.status == "booked"

        processed_event = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "PAYMENT_WEBHOOK_PROCESSED",
                )
                .order_by(AuditLog.id.desc())
            )
        ).scalars().first()
        assert processed_event is not None
        assert processed_event.actor_type == "admin"
        assert processed_event.actor_id == 77
        assert processed_event.new_value["manual_reprocess"] is True
        assert (
            processed_event.new_value["previous_processing_outcome"]
            == PaymentProcessingOutcome.CONFLICT.value
        )
        assert processed_event.new_value["source"] == "admin_manual_confirm"
        assert await _count_action(
            session,
            case_id=case.id,
            action="PAYMENT_PAID",
        ) == 1

    await engine.dispose()


async def _seed_assignment_case(session, *, with_slot: bool = False):
    user = User(
        telegram_id=920002,
        telegram_username="assignment_integrity_client",
        full_name="Клиент проверки назначения",
    )
    slot_lawyer = Lawyer(
        full_name="Юрист выбранного слота",
        email="slot-lawyer@example.test",
        is_active=True,
    )
    other_lawyer = Lawyer(
        full_name="Другой активный юрист",
        email="other-lawyer@example.test",
        is_active=True,
    )
    inactive_lawyer = Lawyer(
        full_name="Неактивный юрист",
        email="inactive-lawyer@example.test",
        is_active=False,
    )
    session.add_all([user, slot_lawyer, other_lawyer, inactive_lawyer])
    await session.flush()

    case = Case(
        case_number="TEST-ASSIGNMENT-INTEGRITY",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Проверка назначения юриста",
        next_action="Ожидать подтверждения",
    )
    session.add(case)
    await session.flush()

    scheduled_at = datetime.now(timezone.utc) + timedelta(days=3)
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=slot_lawyer.id,
        status=ConsultationStatus.PAID_PENDING_CONFIRMATION.value,
        scheduled_at=scheduled_at,
        client_description="Проверка целостности назначения.",
    )
    session.add(consultation)
    await session.flush()

    slot_id = None
    if with_slot:
        slot = ConsultationSlot(
            lawyer_id=slot_lawyer.id,
            starts_at=scheduled_at,
            ends_at=scheduled_at + timedelta(hours=1),
            status="booked",
            held_by_user_id=user.id,
            consultation_id=consultation.id,
        )
        session.add(slot)
        await session.flush()
        consultation.slot_id = slot.id
        slot_id = slot.id

    await session.commit()
    return (
        case.id,
        consultation.id,
        slot_id,
        slot_lawyer.id,
        other_lawyer.id,
        inactive_lawyer.id,
    )


@pytest.mark.asyncio
async def test_assignment_rejects_inactive_and_consultation_mismatched_lawyers(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "assignment-integrity.db",
    )

    async with session_factory() as session:
        (
            case_id,
            _consultation_id,
            _slot_id,
            slot_lawyer_id,
            other_lawyer_id,
            inactive_lawyer_id,
        ) = await _seed_assignment_case(session)

    async with session_factory() as session:
        case = await session.get(Case, case_id)
        service = CaseService(session)

        with pytest.raises(CaseAssignmentError, match="Активный юрист"):
            await service.assign_lawyer(
                case=case,
                lawyer_id=inactive_lawyer_id,
                actor_id=41,
            )

        with pytest.raises(CaseAssignmentError, match="консультацией другого юриста"):
            await service.assign_lawyer(
                case=case,
                lawyer_id=other_lawyer_id,
                actor_id=41,
            )

        assert case.assigned_lawyer_id is None
        assert await _count_action(
            session,
            case_id=case.id,
            action="LAWYER_ASSIGNED",
        ) == 0

        assigned = await service.assign_lawyer(
            case=case,
            lawyer_id=slot_lawyer_id,
            actor_id=41,
        )
        await session.commit()

        assert assigned.assigned_lawyer_id == slot_lawyer_id
        assignment_event = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "LAWYER_ASSIGNED",
                )
            )
        ).scalars().one()
        assert assignment_event.actor_type == "admin"
        assert assignment_event.actor_id == 41

        repeated = await service.assign_lawyer(
            case=case,
            lawyer_id=slot_lawyer_id,
            actor_id=999,
        )
        await session.commit()

        assert repeated.assigned_lawyer_id == slot_lawyer_id
        assert await _count_action(
            session,
            case_id=case.id,
            action="LAWYER_ASSIGNED",
        ) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_assignment_rejects_corrupted_slot_ownership(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "assignment-slot-integrity.db",
    )

    async with session_factory() as session:
        (
            case_id,
            consultation_id,
            slot_id,
            slot_lawyer_id,
            other_lawyer_id,
            _inactive_lawyer_id,
        ) = await _seed_assignment_case(session, with_slot=True)
        slot = await session.get(ConsultationSlot, slot_id)
        slot.lawyer_id = other_lawyer_id
        await session.commit()

    async with session_factory() as session:
        case = await session.get(Case, case_id)
        consultation = await session.get(Consultation, consultation_id)
        assert consultation.slot_id == slot_id

        with pytest.raises(CaseAssignmentError, match="слотом другого юриста"):
            await CaseService(session).assign_lawyer(
                case=case,
                lawyer_id=slot_lawyer_id,
                actor_id=42,
            )

        assert case.assigned_lawyer_id is None
        assert await _count_action(
            session,
            case_id=case.id,
            action="LAWYER_ASSIGNED",
        ) == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_assignment_rejects_closed_case(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "closed-case-assignment.db",
    )

    async with session_factory() as session:
        user = User(
            telegram_id=920003,
            telegram_username="closed_assignment_client",
            full_name="Клиент закрытого дела",
        )
        lawyer = Lawyer(
            full_name="Юрист закрытого дела",
            email="closed-case-lawyer@example.test",
            is_active=True,
        )
        session.add_all([user, lawyer])
        await session.flush()
        case = Case(
            case_number="TEST-CLOSED-ASSIGNMENT",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_CLOSED.value,
            title="Закрытое дело",
            next_action="Дело завершено",
        )
        session.add(case)
        await session.commit()
        case_id = case.id
        lawyer_id = lawyer.id

    async with session_factory() as session:
        case = await session.get(Case, case_id)
        with pytest.raises(CaseAssignmentError, match="закрытое дело"):
            await CaseService(session).assign_lawyer(
                case=case,
                lawyer_id=lawyer_id,
                actor_id=55,
            )
        assert case.assigned_lawyer_id is None

    await engine.dispose()
