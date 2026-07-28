from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.client_context_service import (
    ClientConsultationContextError,
    ClientConsultationContextService,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.user import User


@pytest.fixture
async def context_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'client-context.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_active_m1_does_not_hide_owned_m2_case(context_db):
    async with context_db() as session:
        user = User(telegram_id=1_008_001, full_name="Клиент M1 и M2")
        session.add(user)
        await session.flush()
        m1 = Case(
            case_number="CONTEXT-M1",
            client_id=user.id,
            route=RouteCode.M1.value,
            status=CaseStatus.M1_LAWYER_REVIEW.value,
        )
        m2 = Case(
            case_number="CONTEXT-M2",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_SLOT_PENDING.value,
        )
        session.add_all([m1, m2])
        await session.flush()
        consultation = Consultation(
            case_id=m2.id,
            status=ConsultationStatus.SLOT_PENDING.value,
        )
        session.add(consultation)
        await session.commit()

        service = ClientConsultationContextService(session)
        loaded_case = await service.load_active_m2_case(client_id=user.id)
        loaded_consultation = await service.load_single_consultation(
            case_id=loaded_case.id,
            statuses={ConsultationStatus.SLOT_PENDING.value},
        )

        assert loaded_case.id == m2.id
        assert loaded_case.route == RouteCode.M2.value
        assert loaded_consultation.id == consultation.id


@pytest.mark.asyncio
async def test_foreign_m2_case_is_not_resolved(context_db):
    async with context_db() as session:
        owner = User(telegram_id=1_008_002, full_name="Владелец M2")
        stranger = User(telegram_id=1_008_003, full_name="Другой клиент")
        session.add_all([owner, stranger])
        await session.flush()
        session.add(
            Case(
                case_number="CONTEXT-FOREIGN",
                client_id=owner.id,
                route=RouteCode.M2.value,
                status=CaseStatus.M2_SLOT_PENDING.value,
            )
        )
        await session.commit()

        with pytest.raises(
            ClientConsultationContextError,
            match="не найдено",
        ):
            await ClientConsultationContextService(
                session
            ).load_active_m2_case(client_id=stranger.id)


@pytest.mark.asyncio
async def test_multiple_active_m2_cases_require_manual_resolution(context_db):
    async with context_db() as session:
        user = User(telegram_id=1_008_004, full_name="Клиент конфликта M2")
        session.add(user)
        await session.flush()
        session.add_all(
            [
                Case(
                    case_number="CONTEXT-M2-A",
                    client_id=user.id,
                    route=RouteCode.M2.value,
                    status=CaseStatus.M2_SLOT_PENDING.value,
                ),
                Case(
                    case_number="CONTEXT-M2-B",
                    client_id=user.id,
                    route=RouteCode.M2.value,
                    status=CaseStatus.M2_DESCRIPTION_PENDING.value,
                ),
            ]
        )
        await session.commit()

        with pytest.raises(
            ClientConsultationContextError,
            match="несколько активных",
        ):
            await ClientConsultationContextService(
                session
            ).load_active_m2_case(client_id=user.id)
