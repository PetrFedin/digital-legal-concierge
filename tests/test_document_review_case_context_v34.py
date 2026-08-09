from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.documents.document_review_context import (
    build_document_review_case_context,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.models import Base
from app.models.case import Case
from app.models.document import Document
from app.models.lawyer import Lawyer
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


async def _seed_case(session, *, status=CaseStatus.M1_LAWYER_REVIEW, route="M1"):
    client = User(telegram_id=981001, full_name="Клиент Контекст")
    lawyer = Lawyer(full_name="Юрист Контекст", is_active=True)
    session.add_all([client, lawyer])
    await session.flush()
    case = Case(
        case_number="M1-CONTEXT-1",
        client_id=client.id,
        route=route,
        status=status,
        title="Контекст проверки",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    return case, lawyer


def _actor(lawyer_id: int):
    return SimpleNamespace(role="lawyer", lawyer_id=lawyer_id)


def _document(case_id: int, *, status, version=1, document_type="DDU"):
    return Document(
        case_id=case_id,
        document_type=document_type,
        title="ДДУ",
        file_name="ddu.pdf",
        file_path="/secure/ddu.pdf",
        version=version,
        status=status,
    )


@pytest.mark.asyncio
async def test_all_approved_m1_context_exposes_explicit_accept_action(tmp_path):
    async with database(tmp_path, "approved-context.db") as factory:
        async with factory() as session:
            case, lawyer = await _seed_case(session)
            session.add(_document(case.id, status=DocumentStatus.APPROVED))
            await session.commit()

            context = await build_document_review_case_context(
                session,
                actor=_actor(lawyer.id),
                case_id=case.id,
            )

            assert context is not None
            assert context["documents_ready"] is True
            assert context["can_accept"] is True
            assert context["primary_action"] == "accept_m1_case"
            assert context["primary_label"] == "Принять дело и открыть договор"


@pytest.mark.asyncio
async def test_on_review_document_stays_primary_before_case_acceptance(tmp_path):
    async with database(tmp_path, "review-context.db") as factory:
        async with factory() as session:
            case, lawyer = await _seed_case(session)
            session.add(_document(case.id, status=DocumentStatus.ON_REVIEW))
            await session.commit()

            context = await build_document_review_case_context(
                session,
                actor=_actor(lawyer.id),
                case_id=case.id,
            )

            assert context is not None
            assert context["documents_on_review"] == 1
            assert context["can_accept"] is False
            assert context["primary_action"] == "review_document"


@pytest.mark.asyncio
async def test_reupload_request_is_wait_state_not_fake_operator_mutation(tmp_path):
    async with database(tmp_path, "replacement-context.db") as factory:
        async with factory() as session:
            case, lawyer = await _seed_case(
                session,
                status=CaseStatus.M1_DOCS_REQUESTED,
            )
            session.add(_document(case.id, status=DocumentStatus.NEEDS_REUPLOAD))
            await session.commit()

            context = await build_document_review_case_context(
                session,
                actor=_actor(lawyer.id),
                case_id=case.id,
            )

            assert context is not None
            assert context["documents_replacement"] == 1
            assert context["primary_action"] == "wait_client_reupload"
            assert context["can_accept"] is False


@pytest.mark.asyncio
async def test_m2_never_exposes_m1_accept_even_with_approved_document(tmp_path):
    async with database(tmp_path, "m2-context.db") as factory:
        async with factory() as session:
            case, lawyer = await _seed_case(
                session,
                route="M2",
                status=CaseStatus.M2_CONSULTATION_BOOKED,
            )
            session.add(_document(case.id, status=DocumentStatus.APPROVED))
            await session.commit()

            context = await build_document_review_case_context(
                session,
                actor=_actor(lawyer.id),
                case_id=case.id,
            )

            assert context is not None
            assert context["can_accept"] is False
            assert context["primary_action"] == "open_case"


@pytest.mark.asyncio
async def test_foreign_lawyer_case_context_is_not_disclosed(tmp_path):
    async with database(tmp_path, "foreign-context.db") as factory:
        async with factory() as session:
            case, _ = await _seed_case(session)
            foreign = Lawyer(full_name="Другой юрист", is_active=True)
            session.add(foreign)
            await session.commit()

            context = await build_document_review_case_context(
                session,
                actor=_actor(foreign.id),
                case_id=case.id,
            )

            assert context is None
