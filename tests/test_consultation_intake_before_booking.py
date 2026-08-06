from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot import bot
from app.bot.screens import consultation_intake
from app.domain.consultations.consultation_intake import (
    ActiveCaseRouteConflict,
    ConsultationDescriptionRequired,
    ConsultationIntakeService,
)
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@asynccontextmanager
async def database(tmp_path, name: str):
    database_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_consultation_starts_with_question_before_slot(tmp_path):
    async with database(tmp_path, "intake-first.db") as session_factory:
        async with session_factory() as session:
            user = User(telegram_id=880001, full_name="Клиент")
            session.add(user)
            await session.flush()

            intake = ConsultationIntakeService(session)
            case, consultation = await intake.get_or_create_context(user)

            assert case.route == "M2"
            assert case.status == CaseStatus.M2_DESCRIPTION_PENDING
            assert consultation.status == ConsultationStatus.DESCRIPTION_PENDING

            with pytest.raises(ConsultationDescriptionRequired):
                await intake.prepare_slot_selection(client=user)

            await intake.save_description(
                client=user,
                description="Нужно проверить условия договора и возможные риски.",
                subject_type="new_or_other",
                related_case_id=None,
            )
            assert case.status == CaseStatus.M2_DOCUMENTS_OPTIONAL
            assert consultation.status == ConsultationStatus.DOCUMENTS_OPTIONAL

            prepared_case, prepared_consultation = await intake.prepare_slot_selection(
                client=user
            )
            assert prepared_case.id == case.id
            assert prepared_consultation.id == consultation.id
            assert case.status == CaseStatus.M2_SLOT_PENDING
            await session.commit()


@pytest.mark.asyncio
async def test_slot_hold_advances_case_and_consultation_together(tmp_path):
    async with database(tmp_path, "intake-slot.db") as session_factory:
        async with session_factory() as session:
            user = User(telegram_id=880002, full_name="Клиент")
            lawyer = Lawyer(full_name="Юрист", is_active=True, workload_limit=10)
            session.add_all([user, lawyer])
            await session.flush()
            starts_at = datetime.now(timezone.utc) + timedelta(days=2)
            slot = ConsultationSlot(
                lawyer_id=lawyer.id,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=1),
                status="available",
            )
            session.add(slot)
            await session.flush()

            intake = ConsultationIntakeService(session)
            case, consultation, _was_booked = await intake.save_description(
                client=user,
                description="Нужно обсудить порядок действий и комплект документов.",
                subject_type="new_or_other",
                related_case_id=None,
            )
            case, consultation, held_slot = await intake.reserve_slot(
                client=user,
                slot_id=slot.id,
                payment_required=True,
            )

            assert case.status == CaseStatus.M2_PAYMENT_PENDING
            assert consultation.status == ConsultationStatus.PAYMENT_PENDING
            assert consultation.slot_id == slot.id
            assert held_slot.status == "held"
            assert held_slot.consultation_id == consultation.id
            await session.commit()


@pytest.mark.asyncio
async def test_no_payment_confirmation_keeps_intake_order_and_books_once(tmp_path):
    async with database(tmp_path, "intake-no-pay.db") as session_factory:
        async with session_factory() as session:
            user = User(telegram_id=880003, full_name="Клиент")
            lawyer = Lawyer(full_name="Юрист", is_active=True, workload_limit=10)
            session.add_all([user, lawyer])
            await session.flush()
            starts_at = datetime.now(timezone.utc) + timedelta(days=3)
            slot = ConsultationSlot(
                lawyer_id=lawyer.id,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=1),
                status="available",
            )
            session.add(slot)
            await session.flush()

            intake = ConsultationIntakeService(session)
            case, consultation, _ = await intake.save_description(
                client=user,
                description="Хочу получить оценку договора до подписания соглашения.",
                subject_type="new_or_other",
                related_case_id=None,
            )
            case, consultation, _ = await intake.reserve_slot(
                client=user,
                slot_id=slot.id,
                payment_required=False,
            )
            consultation, booked_slot = await intake.confirm_without_payment(
                client=user,
                case=case,
                consultation=consultation,
            )
            same_consultation, same_slot = await intake.confirm_without_payment(
                client=user,
                case=case,
                consultation=consultation,
            )

            assert case.status == CaseStatus.M2_CONSULTATION_BOOKED
            assert consultation.status == ConsultationStatus.BOOKED
            assert booked_slot.status == "booked"
            assert same_consultation.id == consultation.id
            assert same_slot.id == booked_slot.id
            await session.commit()


@pytest.mark.asyncio
async def test_editing_question_after_booking_preserves_booking(tmp_path):
    async with database(tmp_path, "intake-edit.db") as session_factory:
        async with session_factory() as session:
            user = User(telegram_id=880004, full_name="Клиент")
            session.add(user)
            await session.flush()
            case = Case(
                case_number="M2-BOOKED-1",
                client_id=user.id,
                route="M2",
                status=CaseStatus.M2_CONSULTATION_BOOKED,
                title="Консультация",
            )
            session.add(case)
            await session.flush()
            consultation = Consultation(
                case_id=case.id,
                status=ConsultationStatus.BOOKED,
                client_description="Исходный вопрос по договору и срокам передачи.",
            )
            session.add(consultation)
            await session.flush()

            saved_case, saved_consultation, was_booked = (
                await ConsultationIntakeService(session).save_description(
                    client=user,
                    description="Обновлённый вопрос с дополнительными обстоятельствами.",
                    subject_type="new_or_other",
                    related_case_id=None,
                )
            )

            assert was_booked is True
            assert saved_case.status == CaseStatus.M2_CONSULTATION_BOOKED
            assert saved_consultation.status == ConsultationStatus.BOOKED
            assert "Обновлённый" in saved_consultation.client_description
            await session.commit()


@pytest.mark.asyncio
async def test_active_m1_case_is_not_reused_or_hidden_by_consultation(tmp_path):
    async with database(tmp_path, "intake-conflict.db") as session_factory:
        async with session_factory() as session:
            user = User(telegram_id=880005, full_name="Клиент")
            session.add(user)
            await session.flush()
            case = Case(
                case_number="M1-ACTIVE-1",
                client_id=user.id,
                route="M1",
                status=CaseStatus.M1_LAWYER_REVIEW,
                title="Активное дело",
            )
            session.add(case)
            await session.flush()

            with pytest.raises(ActiveCaseRouteConflict):
                await ConsultationIntakeService(session).get_or_create_context(user)

            assert case.route == "M1"
            assert case.status == CaseStatus.M1_LAWYER_REVIEW


@pytest.mark.asyncio
async def test_payment_domain_rejects_legacy_slot_without_question(tmp_path):
    async with database(tmp_path, "intake-payment.db") as session_factory:
        async with session_factory() as session:
            user = User(telegram_id=880006, full_name="Клиент")
            lawyer = Lawyer(full_name="Юрист", is_active=True, workload_limit=10)
            session.add_all([user, lawyer])
            await session.flush()
            case = Case(
                case_number="M2-PAY-1",
                client_id=user.id,
                route="M2",
                status=CaseStatus.M2_PAYMENT_PENDING,
                title="Консультация",
            )
            session.add(case)
            await session.flush()
            consultation = Consultation(
                case_id=case.id,
                status=ConsultationStatus.PAYMENT_PENDING,
                client_description=None,
            )
            session.add(consultation)
            await session.flush()
            starts_at = datetime.now(timezone.utc) + timedelta(days=2)
            slot = ConsultationSlot(
                lawyer_id=lawyer.id,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=1),
                status="held",
                held_by_user_id=user.id,
                consultation_id=consultation.id,
                hold_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
            session.add(slot)
            await session.flush()
            consultation.slot_id = slot.id

            with pytest.raises(ConsultationDescriptionRequired):
                await PaymentService(session).get_or_create_payment(
                    case=case,
                    payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
                )


def test_slot_compare_and_set_uses_database_fetch_synchronization():
    source = read("app/domain/consultations/slot_service.py")

    assert 'synchronize_session="fetch"' in source
    assert "ConsultationSlot.hold_expires_at >= datetime.now(timezone.utc)" in source
    assert "self._bulk(" in source


def test_canonical_router_precedes_payment_and_legacy_consultation_handlers():
    source = inspect.getsource(bot.build_dispatcher)
    compact = "".join(source.split())

    assert compact.index("consultation_intake.router") < compact.index(
        "no_payment.router"
    )
    assert compact.index("consultation_intake.router") < compact.index(
        "payments.router"
    )
    assert compact.index("consultation_intake.router") < compact.index(
        "consultations.router"
    )


def test_telegram_intake_has_no_booking_before_question_or_dead_end():
    source = inspect.getsource(consultation_intake)

    assert "ConsultationDescriptionRequired" in source
    assert "Сначала опишите вопрос" in source
    assert "Продолжить без документов" in source
    assert "payment_required=not payments_disabled()" in source
    assert "Вопрос и документы не изменены" in source
    assert "Вопрос и документы уже сохранены" in source
    assert "Открыть запись и подготовку" in source
    assert "Повторить" in source
    assert "message_create" in source
    assert "my_case_open" in source
    assert "nav_home" in source
    assert source.index("save_description") < source.index("choose_slot")


def test_payment_success_no_longer_asks_for_question_after_booking():
    source = read("app/bot/screens/payments.py")

    assert "Вопрос уже сохранён" in source
    assert "Открыть запись и подготовку" in source
    assert "Теперь выберите, к какому делу относится встреча" not in source
    assert "ConsultationDescriptionRequired" in source
    assert "Описать вопрос" in source
