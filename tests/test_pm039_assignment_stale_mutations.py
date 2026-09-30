from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.case_assignment import _required_assignment_snapshot
from app.domain.cases.assignment_service import CaseAssignmentService
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.user import User
from app.system.settings_service import SettingsService


async def create_database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def seed(session, suffix: int):
    client = User(
        telegram_id=7_930_000 + suffix,
        full_name=f"Assignment stale client {suffix}",
    )
    admin = AdminUser(
        full_name=f"Assignment admin {suffix}",
        username=f"assignment-admin-{suffix}",
        email=f"assignment-admin-{suffix}@example.test",
        role="admin",
        is_active=True,
    )
    lawyer1 = Lawyer(
        full_name=f"Assignment lawyer A {suffix}",
        email=f"assignment-lawyer-a-{suffix}@example.test",
        is_active=True,
        workload_limit=10,
    )
    lawyer2 = Lawyer(
        full_name=f"Assignment lawyer B {suffix}",
        email=f"assignment-lawyer-b-{suffix}@example.test",
        is_active=True,
        workload_limit=10,
    )
    lawyer1_login = AdminUser(
        full_name=lawyer1.full_name,
        username=f"assignment-lawyer-a-{suffix}",
        email=lawyer1.email,
        role="lawyer",
        is_active=True,
    )
    lawyer2_login = AdminUser(
        full_name=lawyer2.full_name,
        username=f"assignment-lawyer-b-{suffix}",
        email=lawyer2.email,
        role="lawyer",
        is_active=True,
    )
    session.add_all(
        [client, admin, lawyer1, lawyer2, lawyer1_login, lawyer2_login]
    )
    await session.flush()
    case = Case(
        case_number=f"ASSIGN-STALE-{suffix}",
        client_id=int(client.id),
        route="M1",
        status="M1_LAWYER_REVIEW",
        title="Stale assignment command",
    )
    session.add(case)
    await session.flush()
    await SettingsService(session).bootstrap_defaults()
    await session.commit()
    return case, admin, lawyer1, lawyer2


@pytest.mark.asyncio
async def test_stale_unassign_cannot_remove_newer_reassignment(tmp_path):
    engine, factory = await create_database(tmp_path, "pm039-stale-unassign.db")
    async with factory() as session:
        case, admin, lawyer1, lawyer2 = await seed(session, 1)
        service = CaseAssignmentService(session)

        await service.assign_case(
            case_id=int(case.id),
            lawyer_id=int(lawyer1.id),
            actor_type="admin",
            actor_id=int(admin.id),
            comment="Initial assignment",
            expected_lawyer_id=None,
            expected_status=str(case.status),
        )
        await session.commit()

        stale_lawyer = int(lawyer1.id)
        stale_status = str(case.status)
        await service.assign_case(
            case_id=int(case.id),
            lawyer_id=int(lawyer2.id),
            actor_type="admin",
            actor_id=int(admin.id),
            comment="Current reassignment",
            expected_lawyer_id=stale_lawyer,
            expected_status=stale_status,
        )
        await session.commit()

        with pytest.raises(ValueError, match="Назначение дела изменилось"):
            await service.unassign_case(
                case_id=int(case.id),
                actor_type="admin",
                actor_id=int(admin.id),
                comment="Stale old-tab unassign",
                expected_lawyer_id=stale_lawyer,
                expected_status=stale_status,
            )
        await session.rollback()
        await session.refresh(case)
        assert int(case.assigned_lawyer_id) == int(lawyer2.id)

    await engine.dispose()


@pytest.mark.asyncio
async def test_exact_manual_assign_and_unassign_retries_are_idempotent(tmp_path):
    engine, factory = await create_database(tmp_path, "pm039-exact-retry.db")
    async with factory() as session:
        case, admin, lawyer1, _lawyer2 = await seed(session, 2)
        service = CaseAssignmentService(session)
        status = str(case.status)

        assigned = await service.assign_case(
            case_id=int(case.id),
            lawyer_id=int(lawyer1.id),
            actor_type="admin",
            actor_id=int(admin.id),
            comment="Exact assignment command",
            expected_lawyer_id=None,
            expected_status=status,
        )
        await session.commit()

        retry = await service.assign_case(
            case_id=int(case.id),
            lawyer_id=int(lawyer1.id),
            actor_type="admin",
            actor_id=int(admin.id),
            comment="Exact assignment command",
            expected_lawyer_id=None,
            expected_status=status,
        )
        assert retry.id == assigned.id
        await session.rollback()

        with pytest.raises(ValueError, match="Назначение дела изменилось"):
            await service.assign_case(
                case_id=int(case.id),
                lawyer_id=int(lawyer1.id),
                actor_type="admin",
                actor_id=int(admin.id) + 1,
                comment="Different stale command",
                expected_lawyer_id=None,
                expected_status=status,
            )
        await session.rollback()

        unassigned = await service.unassign_case(
            case_id=int(case.id),
            actor_type="admin",
            actor_id=int(admin.id),
            comment="Exact unassign command",
            expected_lawyer_id=int(lawyer1.id),
            expected_status=status,
        )
        await session.commit()
        assert unassigned.assigned_lawyer_id is None

        retry_unassign = await service.unassign_case(
            case_id=int(case.id),
            actor_type="admin",
            actor_id=int(admin.id),
            comment="Exact unassign command",
            expected_lawyer_id=int(lawyer1.id),
            expected_status=status,
        )
        assert retry_unassign.assigned_lawyer_id is None

        with pytest.raises(ValueError, match="Назначение дела изменилось"):
            await service.unassign_case(
                case_id=int(case.id),
                actor_type="admin",
                actor_id=int(admin.id),
                comment="Different stale unassign",
                expected_lawyer_id=int(lawyer1.id),
                expected_status=status,
            )

    await engine.dispose()


def test_interactive_assignment_api_requires_exact_snapshot() -> None:
    assert _required_assignment_snapshot(
        {
            "expected_lawyer_id": None,
            "expected_status": "M1_LAWYER_REVIEW",
        },
        require_assigned_lawyer=False,
    ) == (None, "M1_LAWYER_REVIEW")

    with pytest.raises(HTTPException) as missing:
        _required_assignment_snapshot({}, require_assigned_lawyer=False)
    assert missing.value.status_code == 409

    with pytest.raises(HTTPException) as unassign_without_owner:
        _required_assignment_snapshot(
            {
                "expected_lawyer_id": None,
                "expected_status": "M1_LAWYER_REVIEW",
            },
            require_assigned_lawyer=True,
        )
    assert unassign_without_owner.value.status_code == 409


def test_unassign_checks_snapshot_after_case_row_lock() -> None:
    source = inspect.getsource(CaseAssignmentService.unassign_case)
    assert source.index("for_update=True") < source.index(
        "self._assert_expected_snapshot("
    )
    assert "await self._is_exact_unassign_retry(" in source
