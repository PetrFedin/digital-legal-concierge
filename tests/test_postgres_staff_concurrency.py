from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.cases.case_service import CaseService
from app.domain.cases.case_transition_policy import CaseTransitionError
from app.domain.documents.document_review_service import (
    DocumentReviewError,
    DocumentReviewService,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.document import Document
from app.models.user import User
from app.security.document_access import DocumentActor


_DATABASE_URL = str(settings.database_url)
pytestmark = pytest.mark.skipif(
    not _DATABASE_URL.startswith(("postgresql", "postgres")),
    reason="PostgreSQL staff row-lock concurrency contract",
)


def _telegram_id() -> int:
    return 8_300_000_000_000 + (uuid.uuid4().int % 1_000_000_000)


async def _seed_staff() -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        first = AdminUser(
            full_name="Concurrency Admin A",
            username=f"race-admin-a-{uuid.uuid4().hex}",
            email=f"race-admin-a-{uuid.uuid4().hex}@example.test",
            role="admin",
            is_active=True,
        )
        second = AdminUser(
            full_name="Concurrency Admin B",
            username=f"race-admin-b-{uuid.uuid4().hex}",
            email=f"race-admin-b-{uuid.uuid4().hex}@example.test",
            role="admin",
            is_active=True,
        )
        db.add_all([first, second])
        await db.flush()
        result = int(first.id), int(second.id)
        await db.commit()
        return result


async def _seed_document_review_case() -> tuple[int, int, str, int, str, int, int]:
    first_admin_id, second_admin_id = await _seed_staff()
    async with AsyncSessionLocal() as db:
        user = User(
            telegram_id=_telegram_id(),
            full_name="Postgres document race client",
        )
        db.add(user)
        await db.flush()
        case = Case(
            case_number=f"DOC-RACE-{uuid.uuid4().hex[:20]}",
            client_id=user.id,
            route="M1",
            status=CaseStatus.M1_DOCUMENTS_RECEIVED,
            title="Concurrent document review",
            next_action=CaseService.get_next_action(CaseStatus.M1_DOCUMENTS_RECEIVED),
        )
        db.add(case)
        await db.flush()
        document = Document(
            case_id=case.id,
            uploaded_by_user_id=user.id,
            document_type="ddu",
            title="ДДУ",
            file_name="race-document.pdf",
            file_path=f"pytest/{uuid.uuid4().hex}.pdf",
            mime_type="application/pdf",
            file_size=1024,
            version=1,
            status=DocumentStatus.ON_REVIEW,
            is_required=True,
        )
        db.add(document)
        await db.flush()
        case_id = int(case.id)
        document_id = int(document.id)
        await db.commit()

    async with AsyncSessionLocal() as db:
        document = await db.get(Document, document_id)
        assert document is not None
        assert document.updated_at is not None
        return (
            case_id,
            document_id,
            str(document.status),
            int(document.version),
            document.updated_at.isoformat(),
            first_admin_id,
            second_admin_id,
        )


async def _review_document(
    *,
    document_id: int,
    admin_id: int,
    decision: str,
    comment: str,
    expected_status: str,
    expected_version: int,
    expected_updated_at: str,
):
    async with AsyncSessionLocal() as db:
        account = await db.get(AdminUser, admin_id)
        assert account is not None
        actor = DocumentActor(
            account=account,
            payload={"uid": admin_id, "roles": ["admin"]},
            role="admin",
        )
        try:
            result = await DocumentReviewService(db).review(
                actor=actor,
                document_id=document_id,
                decision=decision,
                comment=comment,
                expected_status=expected_status,
                expected_version=expected_version,
                expected_updated_at=expected_updated_at,
            )
            await db.commit()
            return str(result.document.status), str(result.document.lawyer_comment or "")
        except Exception:
            await db.rollback()
            raise


async def _scenario_conflicting_document_reviews_have_one_winner() -> None:
    (
        case_id,
        document_id,
        expected_status,
        expected_version,
        expected_updated_at,
        first_admin_id,
        second_admin_id,
    ) = await _seed_document_review_case()

    results = await asyncio.gather(
        _review_document(
            document_id=document_id,
            admin_id=first_admin_id,
            decision="approve",
            comment="Документ принят",
            expected_status=expected_status,
            expected_version=expected_version,
            expected_updated_at=expected_updated_at,
        ),
        _review_document(
            document_id=document_id,
            admin_id=second_admin_id,
            decision="request_reupload",
            comment="Нужна новая версия документа",
            expected_status=expected_status,
            expected_version=expected_version,
            expected_updated_at=expected_updated_at,
        ),
        return_exceptions=True,
    )

    successes = [item for item in results if not isinstance(item, BaseException)]
    failures = [item for item in results if isinstance(item, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], DocumentReviewError)

    async with AsyncSessionLocal() as db:
        document = await db.get(Document, document_id)
        case = await db.get(Case, case_id)
        assert document is not None
        assert case is not None

        if str(document.status) == DocumentStatus.APPROVED.value:
            assert document.lawyer_comment == "Документ принят"
            assert str(case.status) == CaseStatus.M1_LAWYER_REVIEW.value
        else:
            assert str(document.status) == DocumentStatus.NEEDS_REUPLOAD.value
            assert document.lawyer_comment == "Нужна новая версия документа"
            assert str(case.status) == CaseStatus.M1_DOCS_REQUESTED.value

        review_decisions = await db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
                AuditLog.action == "DOCUMENT_REVIEW_DECISION",
            )
        )
        assert int(review_decisions or 0) == 1


async def _seed_branching_case() -> tuple[int, int, int]:
    first_admin_id, second_admin_id = await _seed_staff()
    async with AsyncSessionLocal() as db:
        user = User(
            telegram_id=_telegram_id(),
            full_name="Postgres Case transition race client",
        )
        db.add(user)
        await db.flush()
        case = Case(
            case_number=f"CASE-RACE-{uuid.uuid4().hex[:20]}",
            client_id=user.id,
            route="M2",
            status=CaseStatus.M2_CONSULTATION_DONE,
            title="Concurrent Case branch",
            next_action=CaseService.get_next_action(CaseStatus.M2_CONSULTATION_DONE),
        )
        db.add(case)
        await db.flush()
        case_id = int(case.id)
        await db.commit()
        return case_id, first_admin_id, second_admin_id


async def _change_case_branch(
    *,
    case_id: int,
    admin_id: int,
    target: CaseStatus,
    barrier: asyncio.Barrier,
) -> str:
    async with AsyncSessionLocal() as db:
        case = await db.get(Case, case_id)
        assert case is not None
        assert str(case.status) == CaseStatus.M2_CONSULTATION_DONE.value
        await barrier.wait()
        try:
            result = await CaseService(db).change_status(
                case=case,
                next_status=target,
                actor_type="admin",
                actor_id=admin_id,
                comment=f"Concurrent staff decision to {target.value}",
            )
            status = str(result.status)
            await db.commit()
            return status
        except Exception:
            await db.rollback()
            raise


async def _scenario_conflicting_case_branches_have_one_winner() -> None:
    case_id, first_admin_id, second_admin_id = await _seed_branching_case()
    barrier = asyncio.Barrier(2)

    results = await asyncio.gather(
        _change_case_branch(
            case_id=case_id,
            admin_id=first_admin_id,
            target=CaseStatus.M2_CLOSED,
            barrier=barrier,
        ),
        _change_case_branch(
            case_id=case_id,
            admin_id=second_admin_id,
            target=CaseStatus.M1_DOCUMENTS_PENDING,
            barrier=barrier,
        ),
        return_exceptions=True,
    )

    successes = [item for item in results if not isinstance(item, BaseException)]
    failures = [item for item in results if isinstance(item, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], CaseTransitionError)

    async with AsyncSessionLocal() as db:
        case = await db.get(Case, case_id)
        assert case is not None
        assert str(case.status) in {
            CaseStatus.M2_CLOSED.value,
            CaseStatus.M1_DOCUMENTS_PENDING.value,
        }
        if str(case.status) == CaseStatus.M2_CLOSED.value:
            assert str(case.route) == "M2"
            assert case.closed_at is not None
        else:
            assert str(case.route) == "M1"
            assert case.closed_at is None

        status_events = await db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
                AuditLog.action == "CASE_STATUS_CHANGED",
            )
        )
        assert int(status_events or 0) == 1


def test_conflicting_document_reviews_have_one_legal_winner_under_postgres() -> None:
    asyncio.run(_scenario_conflicting_document_reviews_have_one_winner())


def test_conflicting_case_branches_have_one_legal_winner_under_postgres() -> None:
    asyncio.run(_scenario_conflicting_case_branches_have_one_winner())
