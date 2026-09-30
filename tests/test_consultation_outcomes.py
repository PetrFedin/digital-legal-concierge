from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.no_show_resolution_service import (
    NoShowResolutionService,
)
from app.domain.consultations.outcome_service import (
    ConsultationOutcomeError,
    ConsultationOutcomeService,
)
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import create_access_token
from app.security.lawyer_access import require_lawyer_actor


async def create_database(tmp_path, name: str):
    path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def create_context(
    session,
    *,
    suffix: int,
    starts_delta: timedelta = timedelta(hours=-2),
):
    account = AdminUser(
        full_name=f"Юрист аккаунт {suffix}",
        username=f"lawyer-outcome-{suffix}",
        email=f"lawyer-outcome-{suffix}@example.com",
        telegram_id=1010000 + suffix,
        password_hash="test-password-hash",
        role="lawyer",
        is_active=True,
    )
    lawyer = Lawyer(
        full_name=f"Юрист карточка {suffix}",
        email=account.email,
        telegram_id=None,
        is_active=True,
    )
    other_lawyer = Lawyer(
        full_name=f"Другой юрист {suffix}",
        email=f"other-lawyer-{suffix}@example.com",
        telegram_id=1020000 + suffix,
        is_active=True,
    )
    user = User(
        telegram_id=1030000 + suffix,
        full_name=f"Клиент исхода {suffix}",
    )
    session.add_all([account, lawyer, other_lawyer, user])
    await session.flush()

    case = Case(
        case_number=f"OUTCOME-{suffix}",
        client_id=user.id,
        assigned_lawyer_id=lawyer.id,
        route="M2",
        status=CaseStatus.M2_CONSULTATION_BOOKED,
        title="Исход консультации",
    )
    session.add(case)
    await session.flush()

    starts_at = datetime.now(timezone.utc) + starts_delta
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="booked",
        held_by_user_id=user.id,
    )
    session.add(slot)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        slot_id=slot.id,
        status=ConsultationStatus.BOOKED,
        scheduled_at=starts_at,
        client_description="Нужно определить дальнейшие действия по делу.",
    )
    session.add(consultation)
    await session.flush()
    slot.consultation_id = consultation.id

    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.PAID,
        provider="yookassa",
        provider_payment_id=f"outcome-payment-{suffix}",
    )
    session.add(payment)
    await session.flush()
    return {
        "account": account,
        "lawyer": lawyer,
        "other_lawyer": other_lawyer,
        "user": user,
        "case": case,
        "slot": slot,
        "consultation": consultation,
        "payment": payment,
    }


@pytest.mark.asyncio
async def test_personal_token_resolves_own_lawyer_and_syncs_telegram(tmp_path):
    engine, factory = await create_database(tmp_path, "lawyer-token.db")
    async with factory() as session:
        context = await create_context(session, suffix=1)
        await session.commit()
        token = create_access_token(
            context["account"].id,
            context["account"].username,
            context["account"].role,
        )

        actor = await require_lawyer_actor(session, token)
        await session.commit()
        await session.refresh(context["lawyer"])

        assert actor.account.id == context["account"].id
        assert actor.lawyer.id == context["lawyer"].id
        assert context["lawyer"].telegram_id == context["account"].telegram_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_only_assigned_lawyer_can_complete_consultation(tmp_path):
    engine, factory = await create_database(tmp_path, "outcome-owner.db")
    async with factory() as session:
        context = await create_context(session, suffix=2)
        await session.commit()
        consultation_id = context["consultation"].id
        other_lawyer_id = context["other_lawyer"].id

        with pytest.raises(ConsultationOutcomeError, match="назначенному юристу"):
            await ConsultationOutcomeService(session).complete(
                consultation_id=consultation_id,
                lawyer_id=other_lawyer_id,
                result="Подробный результат консультации с дальнейшими шагами.",
                decision="other",
                expected_slot_id=context["slot"].id,
            )
        await session.rollback()

        consultation = await session.get(Consultation, consultation_id)
        assert consultation.status == ConsultationStatus.BOOKED

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_consultation_closes_case_and_slot(tmp_path):
    engine, factory = await create_database(tmp_path, "outcome-complete.db")
    async with factory() as session:
        context = await create_context(session, suffix=3)
        await session.commit()

        consultation = await ConsultationOutcomeService(session).complete(
            consultation_id=context["consultation"].id,
            lawyer_id=context["lawyer"].id,
            result=(
                "Клиенту разъяснены риски, сроки и порядок дальнейших действий."
            ),
            decision="close",
            expected_slot_id=context["slot"].id,
        )
        await session.commit()
        slot = await session.get(ConsultationSlot, context["slot"].id)
        case = await session.get(Case, context["case"].id)

        assert consultation.status == ConsultationStatus.DONE
        assert consultation.decision == "close"
        assert slot.status == "completed"
        assert case.status == CaseStatus.M2_CLOSED

        audit = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "CONSULTATION_COMPLETED",
                )
                .order_by(AuditLog.id.desc())
            )
        ).scalars().first()
        assert audit.actor_id == context["lawyer"].id

    await engine.dispose()


@pytest.mark.asyncio
async def test_client_no_show_requires_fifteen_minutes(tmp_path):
    engine, factory = await create_database(tmp_path, "no-show-early.db")
    async with factory() as session:
        context = await create_context(
            session,
            suffix=4,
            starts_delta=timedelta(minutes=-5),
        )
        await session.commit()
        consultation_id = context["consultation"].id
        lawyer_id = context["lawyer"].id

        with pytest.raises(ConsultationOutcomeError, match="через 15 минут"):
            await ConsultationOutcomeService(session).mark_client_no_show(
                consultation_id=consultation_id,
                lawyer_id=lawyer_id,
                comment="Клиент не подключился к назначенному времени",
                expected_slot_id=context["slot"].id,
            )
        await session.rollback()
        consultation = await session.get(Consultation, consultation_id)
        assert consultation.status == ConsultationStatus.BOOKED

    await engine.dispose()


@pytest.mark.asyncio
async def test_client_no_show_is_recorded_after_delay(tmp_path):
    engine, factory = await create_database(tmp_path, "no-show-client.db")
    async with factory() as session:
        context = await create_context(
            session,
            suffix=5,
            starts_delta=timedelta(minutes=-30),
        )
        await session.commit()

        consultation = await ConsultationOutcomeService(
            session
        ).mark_client_no_show(
            consultation_id=context["consultation"].id,
            lawyer_id=context["lawyer"].id,
            comment="Клиент не подключился и не ответил на сообщение",
            expected_slot_id=context["slot"].id,
        )
        await session.commit()
        slot = await session.get(ConsultationSlot, context["slot"].id)
        case = await session.get(Case, context["case"].id)

        assert consultation.status == ConsultationStatus.CLIENT_NO_SHOW
        assert slot.status == "client_no_show"
        assert case.status == CaseStatus.M2_CONSULTATION_DONE
        assert "Связаться с клиентом" in case.next_action

    await engine.dispose()


@pytest.mark.asyncio
async def test_lawyer_no_show_can_be_rebooked_without_new_payment(tmp_path):
    engine, factory = await create_database(tmp_path, "no-show-rebook.db")
    async with factory() as session:
        context = await create_context(
            session,
            suffix=6,
            starts_delta=timedelta(hours=-2),
        )
        new_starts_at = datetime.now(timezone.utc) + timedelta(days=2)
        new_slot = ConsultationSlot(
            lawyer_id=context["other_lawyer"].id,
            starts_at=new_starts_at,
            ends_at=new_starts_at + timedelta(hours=1),
            status="available",
        )
        session.add(new_slot)
        await session.commit()

        await ConsultationOutcomeService(session).mark_lawyer_no_show(
            consultation_id=context["consultation"].id,
            admin_id=6006,
            comment="Юрист не вышел на связь в назначенное время",
        )
        await session.commit()

        consultation = await ConsultationOutcomeService(
            session
        ).rebook_after_lawyer_no_show(
            consultation_id=context["consultation"].id,
            new_slot_id=new_slot.id,
            admin_id=6006,
            comment="Согласован бесплатный перенос с клиентом",
        )
        await session.commit()
        await session.refresh(new_slot)
        payment = await session.get(Payment, context["payment"].id)
        old_slot = await session.get(ConsultationSlot, context["slot"].id)

        assert consultation.status == ConsultationStatus.BOOKED
        assert consultation.slot_id == new_slot.id
        assert consultation.lawyer_id == context["other_lawyer"].id
        assert new_slot.status == "booked"
        assert old_slot.status == "lawyer_no_show"
        assert old_slot.consultation_id is None
        assert payment.status == PaymentStatus.PAID

    await engine.dispose()


@pytest.mark.asyncio
async def test_lawyer_no_show_can_be_routed_to_refund(tmp_path):
    engine, factory = await create_database(tmp_path, "no-show-refund.db")
    async with factory() as session:
        context = await create_context(
            session,
            suffix=7,
            starts_delta=timedelta(hours=-2),
        )
        await session.commit()

        await ConsultationOutcomeService(session).mark_lawyer_no_show(
            consultation_id=context["consultation"].id,
            admin_id=7007,
            comment="Юрист не вышел на связь в назначенное время",
        )
        await session.commit()

        consultation, payment = await NoShowResolutionService(
            session
        ).route_lawyer_no_show_to_refund(
            consultation_id=context["consultation"].id,
            admin_id=7007,
            comment="Клиент выбрал возврат вместо бесплатного переноса",
        )
        await session.commit()
        case = await session.get(Case, context["case"].id)

        assert consultation.status == ConsultationStatus.CANCELLED
        assert consultation.slot_id is None
        assert payment.status == PaymentStatus.REFUND_PENDING
        assert "возврат" in case.next_action.lower()

    await engine.dispose()
