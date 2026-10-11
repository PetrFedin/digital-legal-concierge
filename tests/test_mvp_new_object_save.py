from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.cases import new_object_service as module


class Savepoint:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return False


class FakeDB:
    def __init__(self):
        self.created = None
        self.select_count = 0

    async def scalar(self, query):
        self.select_count += 1
        return self.created

    def begin_nested(self):
        return Savepoint()


class FakeCases:
    def __init__(self, db):
        self.db = db

    async def get_active_case_for_user(self, user_id):
        return SimpleNamespace(id=19)

    async def create_case(self, **kwargs):
        case = SimpleNamespace(
            id=88,
            client_id=kwargs["client"].id,
            creation_request_key=kwargs["creation_request_key"],
            status="NEW",
        )
        self.db.created = case
        return case


class FakeCalculator:
    def __init__(self, db):
        self.db = db

    def calculate_preview(self, **kwargs):
        assert kwargs["contract_price"] == Decimal("2500000")
        return SimpleNamespace(penalty_amount=Decimal("100"))

    async def calculate_and_save(self, **kwargs):
        self.db.created.status = "CALCULATED"


def arguments():
    return dict(
        user=SimpleNamespace(id=7),
        preview_case_id=19,
        request_key="a" * 32,
        contract_price=Decimal("2500000"),
        planned_transfer_date=date(2026, 1, 1),
        calculation_date=date(2026, 2, 1),
        object_transferred=False,
        actual_transfer_date=None,
    )


@pytest.mark.asyncio
async def test_save_creates_one_separate_case_and_replay_reuses_it(monkeypatch):
    monkeypatch.setattr(module, "CaseService", FakeCases)
    monkeypatch.setattr(module, "CalculatorService", FakeCalculator)
    db = FakeDB()
    service = module.NewObjectSaveService(db)

    first = await service.save(**arguments())
    second = await service.save(**arguments())
    assert first is second
    assert first.creation_request_key == "new-object:7:" + "a" * 32
    assert first.status == "CALCULATED"
    assert db.select_count == 2


@pytest.mark.asyncio
async def test_changed_matter_refuses_creation(monkeypatch):
    monkeypatch.setattr(module, "CaseService", FakeCases)
    db = FakeDB()
    with pytest.raises(module.NewObjectSaveError, match="изменилось"):
        await module.NewObjectSaveService(db).save(
            **{**arguments(), "preview_case_id": 99}
        )
    assert db.created is None


@pytest.mark.asyncio
async def test_forged_preview_key_is_rejected(monkeypatch):
    monkeypatch.setattr(module, "CaseService", FakeCases)
    for token in ("", "a" * 31, "x" * 32):
        with pytest.raises(module.NewObjectSaveError, match="идентификатор"):
            await module.NewObjectSaveService(FakeDB()).save(
                **{**arguments(), "request_key": token}
            )
