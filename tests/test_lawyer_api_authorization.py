import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.lawyer import (
    accept,
    request_docs,
    resolve_authenticated_lawyer,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.user import User
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    create_access_token,
)


async def _create_test_database(tmp_path, name: str):
    database_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def _seed_lawyer_workspace(session):
    user = User(
        telegram_id=980_001,
        telegram_username="lawyer_auth_client",
        full_name="Клиент для проверки кабинета юриста",
    )
    authenticated_lawyer = Lawyer(
        full_name="Назначенный юрист",
        email="assigned-lawyer@example.test",
        is_active=True,
    )
    other_lawyer = Lawyer(
        full_name="Другой юрист",
        email="other-lawyer@example.test",
        is_active=True,
    )
    session.add_all([user, authenticated_lawyer, other_lawyer])
    await session.flush()

    admin_user = AdminUser(
        full_name="Аккаунт назначенного юриста",
        username="assigned_lawyer",
        email="ASSIGNED-LAWYER@example.test",
        password_hash="test-password-hash",
        role=ROLE_LAWYER,
        is_active=True,
    )
    admin_only_user = AdminUser(
        full_name="Администратор без роли юриста",
        username="admin_only",
        email="admin-only@example.test",
        password_hash="test-password-hash",
        role=ROLE_ADMIN,
        is_active=True,
    )
    session.add_all([admin_user, admin_only_user])
    await session.flush()

    owned_case = Case(
        case_number="TEST-LAWYER-AUTH-OWNED",
        client_id=user.id,
        route=RouteCode.M1.value,
        status=CaseStatus.M1_LAWYER_REVIEW.value,
        title="Дело назначенного юриста",
        assigned_lawyer_id=authenticated_lawyer.id,
        next_action="Ожидать проверки юристом",
    )
    foreign_case = Case(
        case_number="TEST-LAWYER-AUTH-FOREIGN",
        client_id=user.id,
        route=RouteCode.M1.value,
        status=CaseStatus.M1_LAWYER_REVIEW.value,
        title="Дело другого юриста",
        assigned_lawyer_id=other_lawyer.id,
        next_action="Ожидать проверки юристом",
    )
    unassigned_case = Case(
        case_number="TEST-LAWYER-AUTH-UNASSIGNED",
        client_id=user.id,
        route=RouteCode.M1.value,
        status=CaseStatus.M1_LAWYER_REVIEW.value,
        title="Неназначенное дело",
        assigned_lawyer_id=None,
        next_action="Ожидать назначения юриста",
    )
    session.add_all([owned_case, foreign_case, unassigned_case])
    await session.commit()

    return {
        "admin_user_id": admin_user.id,
        "admin_only_user_id": admin_only_user.id,
        "lawyer_id": authenticated_lawyer.id,
        "other_lawyer_id": other_lawyer.id,
        "owned_case_id": owned_case.id,
        "foreign_case_id": foreign_case.id,
        "unassigned_case_id": unassigned_case.id,
    }


def _lawyer_token(admin_user_id: int) -> str:
    return create_access_token(
        admin_user_id,
        "assigned_lawyer",
        [ROLE_LAWYER],
    )


def _admin_token(admin_user_id: int) -> str:
    return create_access_token(
        admin_user_id,
        "admin_only",
        [ROLE_ADMIN],
    )


@pytest.mark.asyncio
async def test_resolve_authenticated_lawyer_matches_email_case_insensitively(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "lawyer-profile-resolution.db",
    )
    async with session_factory() as session:
        ids = await _seed_lawyer_workspace(session)

    async with session_factory() as session:
        lawyer = await resolve_authenticated_lawyer(
            token=_lawyer_token(ids["admin_user_id"]),
            db=session,
        )
        assert lawyer.id == ids["lawyer_id"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_without_lawyer_role_cannot_use_lawyer_actions(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "lawyer-role-required.db",
    )
    async with session_factory() as session:
        ids = await _seed_lawyer_workspace(session)

    async with session_factory() as session:
        with pytest.raises(HTTPException) as error:
            await resolve_authenticated_lawyer(
                token=_admin_token(ids["admin_only_user_id"]),
                db=session,
            )
        assert error.value.status_code == 403
        assert "только юристу" in str(error.value.detail)

    await engine.dispose()


@pytest.mark.asyncio
async def test_accept_uses_authenticated_lawyer_and_writes_actor_to_audit(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "lawyer-accept-owned-case.db",
    )
    async with session_factory() as session:
        ids = await _seed_lawyer_workspace(session)

    async with session_factory() as session:
        response = await accept(
            case_id=ids["owned_case_id"],
            db=session,
            x_admin_token=_lawyer_token(ids["admin_user_id"]),
        )

        case = await session.get(Case, ids["owned_case_id"])
        audit_events = list(
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.entity_id == case.id)
                    .order_by(AuditLog.id.asc())
                )
            )
            .scalars()
            .all()
        )

        assert response["lawyer_id"] == ids["lawyer_id"]
        assert response["case_status"] == CaseStatus.M1_CONTRACT_READY.value
        assert case.status == CaseStatus.M1_CONTRACT_READY.value
        assert len(audit_events) == 2
        assert all(event.actor_type == "lawyer" for event in audit_events)
        assert all(event.actor_id == ids["lawyer_id"] for event in audit_events)

    await engine.dispose()


@pytest.mark.asyncio
async def test_lawyer_cannot_accept_case_assigned_to_another_lawyer(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "lawyer-foreign-case-rejected.db",
    )
    async with session_factory() as session:
        ids = await _seed_lawyer_workspace(session)

    async with session_factory() as session:
        with pytest.raises(HTTPException) as error:
            await accept(
                case_id=ids["foreign_case_id"],
                db=session,
                x_admin_token=_lawyer_token(ids["admin_user_id"]),
            )
        assert error.value.status_code == 403
        assert "другому юристу" in str(error.value.detail)

        case = await session.get(Case, ids["foreign_case_id"])
        audit_count = len(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.entity_id == case.id)
                )
            )
            .scalars()
            .all()
        )
        assert case.status == CaseStatus.M1_LAWYER_REVIEW.value
        assert case.assigned_lawyer_id == ids["other_lawyer_id"]
        assert audit_count == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_lawyer_cannot_request_documents_for_unassigned_case(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "lawyer-unassigned-case-rejected.db",
    )
    async with session_factory() as session:
        ids = await _seed_lawyer_workspace(session)

    async with session_factory() as session:
        with pytest.raises(HTTPException) as error:
            await request_docs(
                case_id=ids["unassigned_case_id"],
                payload={"comment": "Нужен договор"},
                db=session,
                x_admin_token=_lawyer_token(ids["admin_user_id"]),
            )
        assert error.value.status_code == 409
        assert "не назначено" in str(error.value.detail)

        case = await session.get(Case, ids["unassigned_case_id"])
        assert case.status == CaseStatus.M1_LAWYER_REVIEW.value
        assert case.assigned_lawyer_id is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_request_documents_records_authenticated_lawyer(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "lawyer-request-documents.db",
    )
    async with session_factory() as session:
        ids = await _seed_lawyer_workspace(session)

    async with session_factory() as session:
        response = await request_docs(
            case_id=ids["owned_case_id"],
            payload={"comment": "Нужна копия договора"},
            db=session,
            x_admin_token=_lawyer_token(ids["admin_user_id"]),
        )

        case = await session.get(Case, ids["owned_case_id"])
        event = (
            await session.execute(
                select(AuditLog).where(AuditLog.entity_id == case.id)
            )
        ).scalar_one()

        assert response["lawyer_id"] == ids["lawyer_id"]
        assert case.status == CaseStatus.M1_DOCS_REQUESTED.value
        assert event.actor_type == "lawyer"
        assert event.actor_id == ids["lawyer_id"]
        assert event.comment == "Нужна копия договора"

    await engine.dispose()
