from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens import consultations
from app.domain.consultations.consultation_change_service import ConsultationChangeService
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User


async def create_database(tmp_path, name: str):
    path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def create_booked_context(session, *, suffix: int, paid: bool):
    lawyer = Lawyer(
        full_name=f"Юрист отмены {suffix}",
        is_active=True,
        workload_limit=10,
    )
    user = User(
        telegram_id=940000 + suffix,
        full_name=f"Клиент отмены {suffix}",
    )
    related_case = Case(
        case_number=f"RELATED-{suffix}",
        client_id=0,
        route="M1",
        status=CaseStatus.M1_CLOSED,
        title="Предыдущее дело",
    )
    session.add_all([lawyer, user])
    await session.flush()
    related_case.client_id = user.id
    session.add(related_case)
    await session.flush()

    case = Case(
        case_number=f"CANCEL-{suffix}",
        client_id=user.id,
        route="M2",
        status=CaseStatus.M2_CONSULTATION_BOOKED,
        title="Консультация",
        next_action="Ожидать консультации",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED,
        subject_type="existing_case",
        related_case_id=related_case.id,
        client_description=(
            "Нужно проверить сроки передачи объекта и последствия дополнительного соглашения."
        ),
    )
    session.add(consultation)
    await session.flush()

    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    consultation.scheduled_at = starts_at

    payment = None
    if paid:
        payment = Payment(
            case_id=case.id,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            title="Оплата консультации",
            amount=Decimal("5000.00"),
            currency="RUB",
            status=PaymentStatus.PAID,
            provider="fake",
            provider_payment_id=f"cancel-paid-{suffix}",
            reservation_key=PaymentService.consultation_reservation_key(
                consultation.id,
                slot.id,
            ),
        )
        session.add(payment)
        await session.flush()

    return {
        "lawyer": lawyer,
        "user": user,
        "related_case": related_case,
        "case": case,
        "consultation": consultation,
        "slot": slot,
        "payment": payment,
    }


@pytest.mark.asyncio
async def test_paid_cancel_preserves_context_and_reopens_slot_selection(tmp_path):
    engine, factory = await create_database(tmp_path, "cancel-rebook-paid.db")
    async with factory() as session:
        context = await create_booked_context(session, suffix=1, paid=True)
        await session.commit()

        cancelled, replacement, case, refund_required = (
            await ConsultationChangeService(session).cancel_and_prepare_rebooking(
                consultation=context["consultation"],
                case=context["case"],
                client_id=context["user"].id,
                comment="Клиент отменил текущую встречу",
                payments_currently_disabled=False,
            )
        )
        await session.commit()

        await session.refresh(cancelled)
        await session.refresh(replacement)
        await session.refresh(case)
        slot = await session.get(ConsultationSlot, context["slot"].id)
        payment = await session.get(Payment, context["payment"].id)

        assert refund_required is True
        assert cancelled.status == ConsultationStatus.CANCELLED
        assert replacement.id != cancelled.id
        assert replacement.status == ConsultationStatus.DOCUMENTS_OPTIONAL
        assert replacement.client_description == context["consultation"].client_description
        assert replacement.subject_type == "existing_case"
        assert replacement.related_case_id == context["related_case"].id
        assert case.status == CaseStatus.M2_SLOT_PENDING
        assert case.next_action == "Выбрать время консультации"
        assert slot.status == "available"
        assert slot.consultation_id is None
        assert payment.status == PaymentStatus.REFUND_PENDING

        current = await ConsultationService(session).get_current_for_case(case.id)
        assert current.id == replacement.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_no_payment_cancel_needs_no_refund_and_still_preserves_context(tmp_path):
    engine, factory = await create_database(tmp_path, "cancel-rebook-no-pay.db")
    async with factory() as session:
        context = await create_booked_context(session, suffix=2, paid=False)
        await session.commit()

        cancelled, replacement, case, refund_required = (
            await ConsultationChangeService(session).cancel_and_prepare_rebooking(
                consultation=context["consultation"],
                case=context["case"],
                client_id=context["user"].id,
                comment="Клиент отменил запись без онлайн-оплаты",
                payments_currently_disabled=True,
            )
        )
        await session.commit()

        slot = await session.get(ConsultationSlot, context["slot"].id)
        payments = (
            await session.execute(select(Payment).where(Payment.case_id == case.id))
        ).scalars().all()

        assert refund_required is False
        assert cancelled.status == ConsultationStatus.CANCELLED
        assert replacement.status == ConsultationStatus.DOCUMENTS_OPTIONAL
        assert replacement.client_description == context["consultation"].client_description
        assert case.status == CaseStatus.M2_SLOT_PENDING
        assert slot.status == "available"
        assert payments == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_existing_paid_payment_still_requires_refund_if_provider_was_disabled_later(tmp_path):
    engine, factory = await create_database(tmp_path, "cancel-provider-changed.db")
    async with factory() as session:
        context = await create_booked_context(session, suffix=3, paid=True)
        await session.commit()

        _cancelled, _replacement, _case, refund_required = (
            await ConsultationChangeService(session).cancel_and_prepare_rebooking(
                consultation=context["consultation"],
                case=context["case"],
                client_id=context["user"].id,
                comment="Клиент отменил после смены конфигурации платежей",
                payments_currently_disabled=True,
            )
        )
        await session.commit()

        payment = await session.get(Payment, context["payment"].id)
        assert refund_required is True
        assert payment.status == PaymentStatus.REFUND_PENDING
    await engine.dispose()


def test_legacy_consultation_router_no_longer_shadows_canonical_intake_handlers():
    source = inspect.getsource(consultations)
    decorator_callbacks = re.findall(
        r"@router\.callback_query\((?:.|\n)*?\)\nasync def ([a-zA-Z0-9_]+)",
        source,
    )

    assert set(decorator_callbacks) == {
        "consult_reschedule",
        "choose_reschedule_date",
        "choose_reschedule_slot",
        "consult_cancel",
        "consult_cancel_confirm",
    }
    assert "ConsultationDescriptionStates" not in source
    assert "@router.message" not in source
    assert "async def booking_start" not in source
    assert "async def choose_slot" not in source
    assert "async def subject_start" not in source
    assert "async def consultation_booked_open" not in source


def test_reschedule_and_cancel_commit_before_recoverable_presentation():
    for handler in (
        consultations.choose_reschedule_slot,
        consultations.consult_cancel_confirm,
    ):
        source = inspect.getsource(handler)
        commit = source.index("await db.commit()")
        safe_edit = source.index("await _safe_edit(", commit)
        assert commit < safe_edit
        assert "callback.message.edit_text" not in source


def test_consultation_change_router_has_navigation_recovery_on_every_terminal_action():
    source = inspect.getsource(consultations)

    assert "consult_booking_start" in source
    assert "consultation_booked_open" in source
    assert "message_create" in source
    assert "my_case_open" in source
    assert "nav_home" in source
    assert "Вопрос и документы сохранены" in source
