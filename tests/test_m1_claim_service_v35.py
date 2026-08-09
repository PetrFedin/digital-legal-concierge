from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.m1_claim_service import M1ClaimService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
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


async def _seed_case(session, status: CaseStatus):
    user = User(telegram_id=982001, full_name="Клиент Претензия")
    lawyer = Lawyer(full_name="Юрист Претензия", is_active=True)
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number=f"M1-CLAIM-{status.value}",
        client_id=user.id,
        route="M1",
        status=status,
        title="Претензионная работа",
        assigned_lawyer_id=lawyer.id,
        next_action="Тестовый следующий шаг",
    )
    session.add(case)
    await session.flush()
    return case, lawyer


@pytest.mark.asyncio
async def test_claim_preparation_and_sent_are_named_sequential_transitions(tmp_path):
    async with database(tmp_path, "claim-flow.db") as factory:
        async with factory() as session:
            case, lawyer = await _seed_case(session, CaseStatus.M1_POA_RECEIVED)
            service = M1ClaimService(session)

            await service.start_claim_preparation(
                case=case,
                lawyer_id=lawyer.id,
                comment="Доверенность и приложения проверены",
            )
            assert case.status == CaseStatus.M1_CLAIM_PREPARATION

            await service.mark_claim_sent(
                case=case,
                lawyer_id=lawyer.id,
                comment="Отправлено ценным письмом, трек 12345",
            )
            assert case.status == CaseStatus.M1_WAITING_30_DAYS
            assert case.next_action == "Ожидать 30 дней после претензии"

            events = list(
                (
                    await session.execute(
                        select(AuditLog)
                        .where(AuditLog.entity_id == case.id)
                        .where(AuditLog.action == "CASE_STATUS_CHANGED")
                        .order_by(AuditLog.id.asc())
                    )
                ).scalars().all()
            )
            statuses = [str((event.new_value or {}).get("status")) for event in events]
            assert CaseStatus.M1_CLAIM_PREPARATION in statuses
            assert CaseStatus.M1_CLAIM_SENT in statuses
            assert CaseStatus.M1_WAITING_30_DAYS in statuses


@pytest.mark.asyncio
async def test_claim_wait_clock_uses_audit_event_not_mutable_case_updated_at(tmp_path):
    async with database(tmp_path, "claim-clock.db") as factory:
        async with factory() as session:
            case, lawyer = await _seed_case(session, CaseStatus.M1_CLAIM_PREPARATION)
            service = M1ClaimService(session)
            await service.mark_claim_sent(
                case=case,
                lawyer_id=lawyer.id,
                comment="Отправлено курьером с подтверждением",
            )
            await session.flush()

            wait_event = (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.entity_id == case.id)
                    .where(AuditLog.action == "CASE_STATUS_CHANGED")
                    .order_by(AuditLog.id.desc())
                    .limit(1)
                )
            ).scalar_one()
            now = datetime.now(timezone.utc)
            wait_event.created_at = now - timedelta(days=31)
            case.updated_at = now
            await session.flush()

            eligibility = await service.court_eligibility(case=case, now=now)

            assert eligibility.eligible is True
            assert eligibility.due_at is not None
            assert eligibility.due_at < now
            assert eligibility.wait_started_at is not None
            assert eligibility.wait_started_at < case.updated_at.replace(tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_court_stage_is_blocked_before_thirty_days(tmp_path):
    async with database(tmp_path, "court-early.db") as factory:
        async with factory() as session:
            case, lawyer = await _seed_case(session, CaseStatus.M1_CLAIM_PREPARATION)
            service = M1ClaimService(session)
            now = datetime.now(timezone.utc)
            await service.mark_claim_sent(
                case=case,
                lawyer_id=lawyer.id,
                comment="Отправлено Почтой России, трек 98765",
            )

            with pytest.raises(ValueError, match="30-дневный срок ещё не истёк"):
                await service.open_court_stage(
                    case=case,
                    lawyer_id=lawyer.id,
                    comment="Готовим иск после претензионного срока",
                    now=now + timedelta(days=29),
                )

            assert case.status == CaseStatus.M1_WAITING_30_DAYS


@pytest.mark.asyncio
async def test_court_stage_opens_after_audited_wait_period(tmp_path):
    async with database(tmp_path, "court-open.db") as factory:
        async with factory() as session:
            case, lawyer = await _seed_case(session, CaseStatus.M1_CLAIM_PREPARATION)
            service = M1ClaimService(session)
            await service.mark_claim_sent(
                case=case,
                lawyer_id=lawyer.id,
                comment="Претензия вручена адресату",
            )
            wait_event = (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.entity_id == case.id)
                    .where(AuditLog.action == "CASE_STATUS_CHANGED")
                    .order_by(AuditLog.id.desc())
                    .limit(1)
                )
            ).scalar_one()
            now = datetime.now(timezone.utc)
            wait_event.created_at = now - timedelta(days=31)
            await session.flush()

            await service.open_court_stage(
                case=case,
                lawyer_id=lawyer.id,
                comment="30 дней истекли, ответ не получен",
                now=now,
            )

            assert case.status == CaseStatus.M1_COURT_STAGE
            assert case.next_action == "Следить за судебным этапом"


@pytest.mark.asyncio
async def test_court_stage_can_open_second_payment_only_as_named_lawyer_action(tmp_path):
    async with database(tmp_path, "court-payment.db") as factory:
        async with factory() as session:
            case, lawyer = await _seed_case(session, CaseStatus.M1_COURT_STAGE)
            service = M1ClaimService(session)

            await service.open_court_payment(
                case=case,
                lawyer_id=lawyer.id,
                comment="Получено определение суда, открыт второй договорный платёж",
            )

            assert case.status == CaseStatus.M1_WAITING_PAYMENT_70000
            assert case.next_action == "Оплатить 70 000 ₽"

            payment = (
                await session.execute(
                    select(Payment).where(
                        Payment.case_id == case.id,
                        Payment.payment_code == PaymentCode.M1_COURT_PAYMENT,
                    )
                )
            ).scalar_one()
            assert payment.status == PaymentStatus.PENDING
            assert Decimal(str(payment.amount)) == Decimal("70000")
            assert payment.payment_url is None


@pytest.mark.asyncio
async def test_foreign_lawyer_cannot_run_claim_transition(tmp_path):
    async with database(tmp_path, "claim-ownership.db") as factory:
        async with factory() as session:
            case, _ = await _seed_case(session, CaseStatus.M1_POA_RECEIVED)
            foreign = Lawyer(full_name="Другой юрист", is_active=True)
            session.add(foreign)
            await session.flush()

            with pytest.raises(ValueError, match="не назначено текущему юристу"):
                await M1ClaimService(session).start_claim_preparation(
                    case=case,
                    lawyer_id=foreign.id,
                )
