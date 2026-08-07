from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.documents.document_review_service import DocumentReviewService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.models import Base
from app.models.case import Case
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.user import User


ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def database(tmp_path, name: str):
    path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_lawyer_case_scope_intersects_assignment_without_disclosure(tmp_path):
    async with database(tmp_path, "document-review-scope.db") as factory:
        async with factory() as session:
            client_a = User(telegram_id=951001, full_name="Клиент А")
            client_b = User(telegram_id=951002, full_name="Клиент Б")
            lawyer_a = Lawyer(full_name="Юрист А", is_active=True)
            lawyer_b = Lawyer(full_name="Юрист Б", is_active=True)
            session.add_all([client_a, client_b, lawyer_a, lawyer_b])
            await session.flush()

            case_a = Case(
                case_number="M1-REVIEW-A",
                client_id=client_a.id,
                route="M1",
                status=CaseStatus.M1_LAWYER_REVIEW,
                title="Дело А",
                assigned_lawyer_id=lawyer_a.id,
            )
            case_b = Case(
                case_number="M1-REVIEW-B",
                client_id=client_b.id,
                route="M1",
                status=CaseStatus.M1_LAWYER_REVIEW,
                title="Дело Б",
                assigned_lawyer_id=lawyer_b.id,
            )
            session.add_all([case_a, case_b])
            await session.flush()

            document_a = Document(
                case_id=case_a.id,
                document_type="DDU",
                title="ДДУ клиента А",
                file_name="a.pdf",
                file_path="/secure/a.pdf",
                version=1,
                status=DocumentStatus.ON_REVIEW,
            )
            document_b = Document(
                case_id=case_b.id,
                document_type="DDU",
                title="ДДУ клиента Б",
                file_name="b.pdf",
                file_path="/secure/b.pdf",
                version=1,
                status=DocumentStatus.ON_REVIEW,
            )
            session.add_all([document_a, document_b])
            await session.commit()

            actor = SimpleNamespace(role="lawyer", lawyer_id=lawyer_a.id)
            service = DocumentReviewService(session)

            own_case = await service.queue(actor=actor, case_id=case_a.id)
            foreign_case = await service.queue(actor=actor, case_id=case_b.id)
            all_assigned = await service.queue(actor=actor)

            assert [item["document_id"] for item in own_case] == [document_a.id]
            assert foreign_case == []
            assert [item["document_id"] for item in all_assigned] == [document_a.id]
            assert all(item["case_id"] == case_a.id for item in all_assigned)


def test_review_api_and_ui_preserve_exact_case_and_recovery_path():
    source = read("app/api/document_review.py")

    assert "case_id: int | None = None" in source
    assert "case_id=case_id" in source
    assert '"scoped": case_id is not None' in source
    assert "new URLSearchParams(location.search)" in source
    assert "queuePath()" in source
    assert "?case_id=" in source
    assert "Документы выбранного дела" in source
    assert "Вся очередь" in source
    assert "дело недоступно в вашей роли" in source
    assert "const pending=new Set(),drafts=new Map()" in source
    assert "drafts.get(draftKey(id,decision))" in source
    assert "drafts.delete(draftKey(id,decision))" in source
    assert "Решение сохранено, но очередь не обновилась" in source
    assert "Решение не сохранено" in source


def test_staff_surfaces_deep_link_review_to_case_context():
    workspace = read("app/api/lawyer_workspace.py")
    consultation_desk = read("app/api/lawyer_consultation_desk.py")

    assert '/document-access/review/ui?case_id=${x.case_id}' in workspace
    assert 'f"/document-access/review/ui?case_id={case.id}"' in consultation_desk
