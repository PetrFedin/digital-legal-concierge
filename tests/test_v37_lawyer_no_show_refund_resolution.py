from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.consultations.no_show_resolution_service import (
    NoShowResolutionError,
    NoShowResolutionService,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus


class _One:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Many:
    def __init__(self, values):
        self.values = list(values)

    def scalars(self):
        return self

    def first(self):
        return self.values[0] if self.values else None

    def all(self):
        return list(self.values)


class _Db:
    def __init__(self, *results):
        self.results = list(results)
        self.added = []

    async def execute(self, _statement):
        if not self.results:
            raise AssertionError("unexpected DB execute")
        return self.results.pop(0)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


def _cancelled_context():
    consultation = SimpleNamespace(
        id=510,
        case_id=610,
        slot_id=None,
        scheduled_at=None,
        status=ConsultationStatus.CANCELLED,
    )
    case = SimpleNamespace(
        id=610,
        status=CaseStatus.M2_CONSULTATION_DONE,
        next_action="Ожидать фактический результат возврата",
        case_number="M2-610",
    )
    payment = SimpleNamespace(
        id=710,
        case_id=610,
        status=PaymentStatus.REFUND_PENDING,
        amount=5000,
    )
    return consultation, case, payment


def _event(*, actor_id: int, comment: str):
    return SimpleNamespace(
        actor_id=actor_id,
        comment=comment,
        new_value={"consultation_id": 510, "payment_id": 710},
    )


def test_cancelled_refund_exact_retry_requires_same_admin_and_comment():
    consultation, case, payment = _cancelled_context()
    db = _Db(_One(consultation), _One(case), _Many([payment]))
    service = NoShowResolutionService(db)  # type: ignore[arg-type]
    service._latest_refund_request_event = AsyncMock(
        return_value=_event(actor_id=91, comment="Возврат согласован после неявки")
    )

    result_consultation, result_payment = asyncio.run(
        service.route_lawyer_no_show_to_refund(
            consultation_id=510,
            admin_id=91,
            comment="Возврат согласован после неявки",
        )
    )

    assert result_consultation is consultation
    assert result_payment is payment
    assert payment.status == PaymentStatus.REFUND_PENDING


def test_cancelled_refund_retry_from_other_admin_is_conflict():
    consultation, case, payment = _cancelled_context()
    db = _Db(_One(consultation), _One(case), _Many([payment]))
    service = NoShowResolutionService(db)  # type: ignore[arg-type]
    service._latest_refund_request_event = AsyncMock(
        return_value=_event(actor_id=91, comment="Возврат согласован после неявки")
    )

    with pytest.raises(NoShowResolutionError, match="другим администратором"):
        asyncio.run(
            service.route_lawyer_no_show_to_refund(
                consultation_id=510,
                admin_id=92,
                comment="Возврат согласован после неявки",
            )
        )


def test_existing_refund_pending_still_cancels_consultation_and_releases_slot_atomically():
    consultation = SimpleNamespace(
        id=511,
        case_id=611,
        slot_id=711,
        scheduled_at=SimpleNamespace(isoformat=lambda: "2026-08-25T12:00:00+00:00"),
        status=ConsultationStatus.LAWYER_NO_SHOW,
    )
    case = SimpleNamespace(
        id=611,
        status=CaseStatus.M2_CONSULTATION_DONE,
        next_action="Выбрать решение после неявки",
        case_number="M2-611",
    )
    payment = SimpleNamespace(
        id=712,
        case_id=611,
        status=PaymentStatus.REFUND_PENDING,
        amount=5000,
    )
    slot = SimpleNamespace(
        id=711,
        consultation_id=511,
        held_by_user_id=100,
        hold_expires_at=SimpleNamespace(),
        status="lawyer_no_show",
    )
    db = _Db(_One(consultation), _One(case), _Many([payment]), _One(slot))
    service = NoShowResolutionService(db)  # type: ignore[arg-type]
    service.notifications.emit = AsyncMock(return_value=[])

    result_consultation, result_payment = asyncio.run(
        service.route_lawyer_no_show_to_refund(
            consultation_id=511,
            admin_id=93,
            comment="Клиент выбрал возврат после неявки юриста",
        )
    )

    assert result_consultation is consultation
    assert result_payment is payment
    assert consultation.status == ConsultationStatus.CANCELLED
    assert consultation.slot_id is None
    assert consultation.scheduled_at is None
    assert slot.status == "available"
    assert slot.consultation_id is None
    assert slot.held_by_user_id is None
    assert slot.hold_expires_at is None
    assert payment.status == PaymentStatus.REFUND_PENDING
    assert case.status == CaseStatus.M2_CONSULTATION_DONE
    assert "фактический результат возврата" in case.next_action
    assert len(db.added) == 1
    assert db.added[0].action == service.REFUND_ACTION
    assert db.added[0].actor_id == 93
    assert db.added[0].new_value["released_slot_id"] == 711


def test_no_show_refund_idempotency_search_has_no_arbitrary_history_limit():
    source = inspect.getsource(NoShowResolutionService._latest_refund_request_event)
    assert "AuditLog.action == self.REFUND_ACTION" in source
    assert "AuditLog.entity_id == int(case_id)" in source
    assert ".limit(" not in source
