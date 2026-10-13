"""Unit boundary: idempotent new-object save must match the displayed result."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.calculator.preview_receipt import preview_receipt
from app.domain.cases import new_object_service as module
from app.models.calculation import Calculation
from app.models.user import User


class Savepoint:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return False


class FakeDB:
    def __init__(self):
        self.created = None
        self.calculation = None
        self.owner = SimpleNamespace(id=7, active_case_id=None)
        self.select_count = 0

    async def scalar(self, query):
        self.select_count += 1
        entity = query.column_descriptions[0]["entity"]
        if entity is User:
            return self.owner
        return self.calculation if entity is Calculation else self.created

    async def flush(self):
        pass

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
        return SimpleNamespace(
            contract_price=kwargs["contract_price"],
            planned_transfer_date=kwargs["planned_transfer_date"],
            calculation_date=kwargs["calculation_date"],
            actual_transfer_date=kwargs["actual_transfer_date"],
            object_transferred=kwargs["object_transferred"],
            delay_days=59,
            key_rate=Decimal("0.16"),
            consumer_multiplier=Decimal("2"),
            penalty_amount=Decimal("157333.33"),
            formula_version="ddu-214fz-v2",
        )

    async def calculate_and_save(self, **kwargs):
        assert kwargs["key_rate"] == Decimal("0.16")
        assert kwargs["consumer_multiplier"] == Decimal("2")
        result = self.calculate_preview(**{
            field: kwargs[field]
            for field in (
                "contract_price",
                "planned_transfer_date",
                "calculation_date",
                "object_transferred",
                "actual_transfer_date",
            )
        })
        self.db.calculation = result
        self.db.created.status = "CALCULATED"


def arguments():
    facts = dict(
        contract_price=Decimal("2500000"),
        planned_transfer_date=date(2026, 1, 1),
        calculation_date=date(2026, 3, 1),
        object_transferred=False,
        actual_transfer_date=None,
    )
    return dict(
        user=SimpleNamespace(id=7),
        preview_case_id=19,
        request_key="a" * 32,
        expected_receipt=preview_receipt(FakeCalculator(None).calculate_preview(**facts)),
        **facts,
    )


@pytest.fixture(autouse=True)
def _fake_services(monkeypatch):
    monkeypatch.setattr(module, "CaseService", FakeCases)
    monkeypatch.setattr(module, "CalculatorService", FakeCalculator)


@pytest.mark.asyncio
async def test_save_creates_one_separate_case_and_replay_reuses_it():
    db = FakeDB()
    service = module.NewObjectSaveService(db)

    first = await service.save(**arguments())
    second = await service.save(**arguments())
    assert first is second
    assert first.creation_request_key == "new-object:7:" + "a" * 32
    assert first.status == "CALCULATED"
    assert db.select_count == 5
    assert db.owner.active_case_id == 19


@pytest.mark.asyncio
async def test_changed_matter_refuses_creation():
    db = FakeDB()
    with pytest.raises(module.NewObjectSaveError, match="изменилось"):
        await module.NewObjectSaveService(db).save(
            **{**arguments(), "preview_case_id": 99}
        )
    assert db.created is None


@pytest.mark.asyncio
async def test_forged_preview_key_is_rejected():
    for token in ("", "a" * 31, "x" * 32):
        with pytest.raises(module.NewObjectSaveError, match="идентификатор"):
            await module.NewObjectSaveService(FakeDB()).save(
                **{**arguments(), "request_key": token}
            )


@pytest.mark.asyncio
async def test_changed_facts_cannot_save_receipt_from_old_preview():
    db = FakeDB()
    data = arguments()
    data["calculation_date"] = date(2026, 3, 2)
    with pytest.raises(module.NewObjectSaveError, match="не соответствуют показанному"):
        await module.NewObjectSaveService(db).save(**data)
    assert db.created is None


@pytest.mark.asyncio
async def test_changed_rate_or_formula_cannot_silently_save():
    for field, changed in (("key_rate", "0.18"), ("formula_version", "new-rule")):
        db = FakeDB()
        data = arguments()
        data["expected_receipt"] = {**data["expected_receipt"], field: changed}
        with pytest.raises(module.NewObjectSaveError, match="Условия расчёта изменились"):
            await module.NewObjectSaveService(db).save(**data)
        assert db.created is None


@pytest.mark.asyncio
async def test_used_token_cannot_be_replayed_with_different_receipt():
    db = FakeDB()
    service = module.NewObjectSaveService(db)
    first = await service.save(**arguments())
    assert first is db.created

    changed = arguments()
    changed["expected_receipt"] = {**changed["expected_receipt"], "penalty_amount": "1"}
    with pytest.raises(module.NewObjectSaveError, match="ключ уже использован"):
        await service.save(**changed)
    assert db.created is first


@pytest.mark.asyncio
async def test_same_receipt_cannot_mask_changed_input_during_retry():
    db = FakeDB()
    service = module.NewObjectSaveService(db)
    first = await service.save(**arguments())
    changed = arguments()
    changed["contract_price"] = Decimal("2600000")
    with pytest.raises(module.NewObjectSaveError, match="не соответствуют показанному"):
        await service.save(**changed)
    assert db.created is first
    assert db.owner.active_case_id == 19
