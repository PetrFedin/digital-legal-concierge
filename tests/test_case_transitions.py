from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.domain.cases.case_service as case_service_module
from app.domain.cases.case_service import CaseService
from app.domain.cases.case_transition_policy import (
    CaseTransitionError,
    assert_policy_complete,
    transition_allowed,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode


class FakeDB:
    def __init__(self):
        self.flush_count = 0

    async def flush(self):
        self.flush_count += 1


class FakeSLAService:
    calls = []

    def __init__(self, _db):
        pass

    async def synchronize_case_status(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture(autouse=True)
def patch_transition_side_effects(monkeypatch):
    events = []

    async def add_event(_db, **kwargs):
        events.append(kwargs)
        return kwargs

    FakeSLAService.calls = []
    monkeypatch.setattr(case_service_module, "add_case_history_event", add_event)
    monkeypatch.setattr(case_service_module, "CaseSLAService", FakeSLAService)
    return events


def make_case(status=CaseStatus.NEW):
    return SimpleNamespace(
        id=10,
        client_id=20,
        status=status,
        route=None,
        next_action="old",
        closed_at=None,
        content_deleted_at=None,
    )


def test_transition_policy_covers_every_declared_status():
    assert_policy_complete()


def test_expected_m1_and_m2_paths_are_allowed():
    assert transition_allowed(CaseStatus.NEW, CaseStatus.CALCULATED)
    assert transition_allowed(
        CaseStatus.M1_WAITING_PAYMENT_30000,
        CaseStatus.M1_PAYMENT_30000_RECEIVED,
    )
    assert transition_allowed(
        CaseStatus.M1_COURT_STAGE,
        CaseStatus.M1_WAITING_PAYMENT_70000,
    )
    assert not transition_allowed(
        CaseStatus.M1_COURT_STAGE,
        CaseStatus.M1_ENFORCEMENT,
    )
    assert not transition_allowed(
        CaseStatus.M1_COURT_STAGE,
        CaseStatus.M1_MONEY_RECEIVED,
    )
    assert transition_allowed(
        CaseStatus.M2_SLOT_PENDING,
        CaseStatus.M2_PAYMENT_PENDING,
    )
    assert not transition_allowed(
        CaseStatus.M2_SLOT_PENDING,
        CaseStatus.M2_CONSULTATION_BOOKED,
    )
    assert transition_allowed(
        CaseStatus.M2_PAYMENT_PENDING,
        CaseStatus.M2_CONSULTATION_BOOKED,
    )
    assert transition_allowed(
        CaseStatus.M2_CONSULTATION_BOOKED,
        CaseStatus.M2_CONSULTATION_DONE,
    )
    assert not transition_allowed(
        CaseStatus.M2_CONSULTATION_BOOKED,
        CaseStatus.M2_CLOSED,
    )
    assert not transition_allowed(
        CaseStatus.M2_CONSULTATION_BOOKED,
        CaseStatus.M1_DOCUMENTS_PENDING,
    )
    assert transition_allowed(
        CaseStatus.M2_CONSULTATION_DONE,
        CaseStatus.M2_TO_M1,
    )
    assert not transition_allowed(
        CaseStatus.M2_CONSULTATION_DONE,
        CaseStatus.M1_DOCUMENTS_PENDING,
    )
    assert transition_allowed(
        CaseStatus.M2_TO_M1,
        CaseStatus.M1_DOCUMENTS_PENDING,
    )
    assert not transition_allowed(
        CaseStatus.M1_CLOSED,
        CaseStatus.M1_DOCUMENTS_PENDING,
    )


async def test_normal_transition_updates_route_history_and_sla(
    patch_transition_side_effects,
):
    db = FakeDB()
    case = make_case(CaseStatus.CLIENT_DECISION)

    await CaseService(db).change_status(
        case=case,
        next_status=CaseStatus.M1_DOCUMENTS_PENDING,
        actor_type="client",
        actor_id=20,
        comment="Клиент выбрал маршрут М1",
    )

    assert case.status == CaseStatus.M1_DOCUMENTS_PENDING
    assert case.route == RouteCode.M1
    assert case.next_action == "Загрузить документы"
    assert db.flush_count == 1
    assert len(patch_transition_side_effects) == 1
    assert patch_transition_side_effects[0]["new_value"]["forced"] is False
    assert len(FakeSLAService.calls) == 1


async def test_same_status_is_idempotent_without_duplicate_history(
    patch_transition_side_effects,
):
    db = FakeDB()
    case = make_case(CaseStatus.M2_CONSULTATION_BOOKED)
    case.route = RouteCode.M2

    await CaseService(db).change_status(
        case=case,
        next_status=CaseStatus.M2_CONSULTATION_BOOKED,
        actor_type="system",
        comment="Повтор webhook",
    )

    assert db.flush_count == 0
    assert patch_transition_side_effects == []
    assert FakeSLAService.calls == []


async def test_invalid_normal_transition_is_rejected_without_mutation(
    patch_transition_side_effects,
):
    db = FakeDB()
    case = make_case(CaseStatus.M1_CLOSED)

    with pytest.raises(CaseTransitionError, match="Недопустимый переход"):
        await CaseService(db).change_status(
            case=case,
            next_status=CaseStatus.M1_DOCUMENTS_PENDING,
            actor_type="client",
            actor_id=20,
            comment="Попытка вернуть закрытое дело",
        )

    assert case.status == CaseStatus.M1_CLOSED
    assert db.flush_count == 0
    assert patch_transition_side_effects == []


async def test_forced_transition_requires_privileged_actor_and_comment(
    patch_transition_side_effects,
):
    db = FakeDB()
    case = make_case(CaseStatus.M1_CLOSED)

    with pytest.raises(CaseTransitionError, match="только администратору"):
        await CaseService(db).change_status(
            case=case,
            next_status=CaseStatus.M1_DOCUMENTS_PENDING,
            actor_type="client",
            force=True,
            comment="Клиент пытается обойти процесс",
        )

    with pytest.raises(CaseTransitionError, match="комментарий"):
        await CaseService(db).change_status(
            case=case,
            next_status=CaseStatus.M1_DOCUMENTS_PENDING,
            actor_type="admin",
            force=True,
            comment="",
        )

    await CaseService(db).change_status(
        case=case,
        next_status=CaseStatus.M1_DOCUMENTS_PENDING,
        actor_type="admin",
        actor_id=1,
        force=True,
        comment="Исправление ошибочно закрытого дела по заявке клиента",
    )

    assert case.status == CaseStatus.M1_DOCUMENTS_PENDING
    assert patch_transition_side_effects[-1]["new_value"]["forced"] is True


async def test_terminal_transition_sets_closed_at_and_forced_reopen_clears_it(
    patch_transition_side_effects,
):
    db = FakeDB()
    case = make_case(CaseStatus.M1_SUCCESS_FEE_RECEIVED)

    await CaseService(db).change_status(
        case=case,
        next_status=CaseStatus.M1_CLOSED,
        actor_type="system",
        comment="Финансовый этап завершён",
    )
    assert case.closed_at is not None
    first_closed_at = case.closed_at

    await CaseService(db).change_status(
        case=case,
        next_status=CaseStatus.M1_DOCUMENTS_PENDING,
        actor_type="admin",
        actor_id=1,
        force=True,
        comment="Подтверждённое исправление ошибочного закрытия дела",
    )
    assert first_closed_at is not None
    assert case.closed_at is None


@pytest.mark.asyncio
async def test_deleted_case_content_cannot_be_reopened(monkeypatch):
    history = []

    async def fake_history(_db, **kwargs):
        history.append(kwargs)

    async def fake_sla(self, **kwargs):
        return kwargs["case"]

    monkeypatch.setattr(case_service_module, "add_case_history_event", fake_history)
    monkeypatch.setattr(
        case_service_module.CaseSLAService,
        "synchronize_case_status",
        fake_sla,
    )
    case = make_case(CaseStatus.M1_CLOSED)
    case.content_deleted_at = datetime.now(timezone.utc)
    with pytest.raises(CaseTransitionError, match="после удаления"):
        await CaseService(FakeDB()).change_status(
            case=case,
            next_status=CaseStatus.M1_DOCUMENTS_PENDING,
            actor_type="admin",
            actor_id=7,
            force=True,
            comment="Ошибочная попытка повторно открыть tombstone",
        )
    assert case.status == CaseStatus.M1_CLOSED
    assert history == []
