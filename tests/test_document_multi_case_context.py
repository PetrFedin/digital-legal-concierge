from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.bot.screens.document_action_center import _active_case
from app.db.session import AsyncSessionLocal
from app.domain.cases.case_service import CaseSelectionRequired, CaseService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models.case import Case
from app.models.user import User


def _telegram_id() -> int:
    return 7_200_000_000_000 + (uuid.uuid4().int % 1_000_000_000)


def _callback(telegram_id: int):
    return SimpleNamespace(
        from_user=SimpleNamespace(
            id=telegram_id,
            username=f"doc_ctx_{telegram_id}",
            full_name="Document Context Client",
        )
    )


async def _create_case(service: CaseService, user: User, *, status, route=None):
    return await service.create_case_for_operation(
        client=user,
        operation_key=f"pytest:document-context:{uuid.uuid4().hex}",
        purpose="document_context_test",
        route=route,
        status=status,
        title="Document context test",
    )


@pytest.mark.asyncio
async def test_document_callback_fails_closed_if_selected_case_closed_and_two_active_remain():
    telegram_id = _telegram_id()
    async with AsyncSessionLocal() as db:
        user = User(
            telegram_id=telegram_id,
            telegram_username=f"doc_ctx_{telegram_id}",
            full_name="Document Context Client",
        )
        db.add(user)
        await db.flush()
        service = CaseService(db)

        first = await _create_case(
            service,
            user,
            status=CaseStatus.CALCULATOR_STARTED,
        )
        second = await _create_case(
            service,
            user,
            status=CaseStatus.CALCULATOR_STARTED,
        )
        selected = await _create_case(
            service,
            user,
            status=CaseStatus.M1_SUCCESS_FEE_RECEIVED,
            route=RouteCode.M1.value,
        )
        await service.change_status(
            case=selected,
            next_status=CaseStatus.M1_CLOSED,
            actor_type="system",
            actor_id=None,
            comment="Document context ambiguity regression",
        )
        await db.commit()

        # Both the document action center and the legacy generic resolver must
        # refuse to guess between first/second after the selected matter closes.
        assert await service.get_active_case_for_user(user.id) is None
        _, resolved = await _active_case(_callback(telegram_id), db)
        assert resolved is None

        active_ids = {
            int(case.id) for case in await service.get_active_cases_for_user(user.id)
        }
        assert active_ids == {int(first.id), int(second.id)}

        with pytest.raises(CaseSelectionRequired):
            await service.get_or_create_active_case_for_user(user)
        # An ambiguity must never be mistaken for "zero active" and create a
        # fourth legal matter as a side effect of an old compatibility flow.
        assert {
            int(case.id) for case in await service.get_active_cases_for_user(user.id)
        } == active_ids


@pytest.mark.asyncio
async def test_document_callback_may_use_only_active_case_when_selection_is_stale():
    telegram_id = _telegram_id()
    async with AsyncSessionLocal() as db:
        user = User(
            telegram_id=telegram_id,
            telegram_username=f"doc_ctx_{telegram_id}",
            full_name="Document Context Client",
        )
        db.add(user)
        await db.flush()
        service = CaseService(db)

        only_active = await _create_case(
            service,
            user,
            status=CaseStatus.CALCULATOR_STARTED,
        )
        selected = await _create_case(
            service,
            user,
            status=CaseStatus.M1_SUCCESS_FEE_RECEIVED,
            route=RouteCode.M1.value,
        )
        await service.change_status(
            case=selected,
            next_status=CaseStatus.M1_CLOSED,
            actor_type="system",
            actor_id=None,
            comment="Document context sole-active fallback regression",
        )
        await db.commit()

        global_resolved = await service.get_active_case_for_user(user.id)
        assert global_resolved is not None
        assert int(global_resolved.id) == int(only_active.id)

        _, resolved = await _active_case(_callback(telegram_id), db)
        assert resolved is not None
        assert int(resolved.id) == int(only_active.id)
