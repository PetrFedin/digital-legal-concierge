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
        CaseStatus.M2_CONSULTATION_DONE,
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
