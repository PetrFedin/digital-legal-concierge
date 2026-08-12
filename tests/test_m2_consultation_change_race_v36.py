from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.consultations.consultation_change_service import ConsultationChangeService
from app.domain.statuses.consultation_statuses import ConsultationStatus


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


class _PaymentResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _Scalars(self.values)


class _FakeDB:
    def __init__(self, result):
        self.result = result
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.result


def _sql(statement) -> str:
    return " ".join(str(statement).upper().split())


@pytest.mark.asyncio
async def test_cancel_rebook_reloads_booked_consultation_under_row_lock():
    consultation = SimpleNamespace(
        id=41,
        case_id=17,
        status=ConsultationStatus.BOOKED,
    )
    db = _FakeDB(_ScalarResult(consultation))
    service = ConsultationChangeService(db)

    locked = await service._lock_booked_consultation(
        case_id=17,
        consultation_id=41,
    )

    assert locked is consultation
    assert "FOR UPDATE" in _sql(db.statements[-1])


@pytest.mark.asyncio
async def test_stale_second_cancel_is_rejected_after_first_transition():
    consultation = SimpleNamespace(
        id=41,
        case_id=17,
        status=ConsultationStatus.CANCELLED,
    )
    db = _FakeDB(_ScalarResult(consultation))
    service = ConsultationChangeService(db)

    with pytest.raises(ValueError, match="уже была изменена"):
        await service._lock_booked_consultation(
            case_id=17,
            consultation_id=41,
        )

    assert "FOR UPDATE" in _sql(db.statements[-1])


@pytest.mark.asyncio
async def test_cancellation_financial_classification_locks_exact_reservation_payments():
    db = _FakeDB(_PaymentResult([]))
    service = ConsultationChangeService(db)

    payments = await service._payments_for_consultation(
        case_id=17,
        consultation_id=41,
    )

    sql = _sql(db.statements[-1])
    assert payments == []
    assert "FOR UPDATE" in sql
    assert "PAYMENTS.CASE_ID" in sql
    assert "PAYMENTS.PAYMENT_CODE" in sql
    assert "PAYMENTS.RESERVATION_KEY LIKE" in sql
