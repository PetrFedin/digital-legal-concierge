from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.refund_service import (
    ConsultationRefundService,
    RefundResolutionConflictError,
)
from app.domain.statuses.payment_statuses import PaymentStatus


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _SequentialDb:
    """Small AsyncSession-shaped double for terminal retry branches.

    The service locks Payment first and Case second. These regression tests do not
    emulate PostgreSQL locking itself (that remains in the PostgreSQL suite); they
    execute the real terminal-state decision code around those two locked rows.
    """

    def __init__(self, payment, case):
        self.values = [payment, case]
        self.execute_count = 0

    async def execute(self, _statement):
        value = self.values[self.execute_count]
        self.execute_count += 1
        return _ScalarResult(value)

    async def flush(self):
        return None


def _payment(*, status: PaymentStatus):
    return SimpleNamespace(
        id=701,
        case_id=801,
        status=status,
        payment_code=PaymentCode.M1_INITIAL_PAYMENT,
        title="Тестовый возврат",
    )


def _case():
    return SimpleNamespace(id=801)


def _audit_event(*, decision: str | None = None, status: str | None = None, actor_id: int, comment: str):
    new_value = {"payment_id": 701}
    if decision is not None:
        new_value["decision"] = decision
    if status is not None:
        new_value["status"] = status
    return SimpleNamespace(
        new_value=new_value,
        actor_id=actor_id,
        comment=comment,
    )


def test_exact_retry_of_completed_refund_is_accepted_without_second_transition():
    payment = _payment(status=PaymentStatus.REFUNDED)
    service = ConsultationRefundService(_SequentialDb(payment, _case()))
    service._latest_payment_history_event = AsyncMock(
        return_value=_audit_event(
            decision="refunded",
            actor_id=11,
            comment="provider operation 12345",
        )
    )

    result = asyncio.run(
        service.resolve_refund(
            payment_id=payment.id,
            decision="refunded",
            actor_id=11,
            comment="provider operation 12345",
        )
    )

    assert result is payment
    assert payment.status == PaymentStatus.REFUNDED
    service._latest_payment_history_event.assert_awaited_once()


def test_same_terminal_refund_from_other_admin_is_a_conflict():
    payment = _payment(status=PaymentStatus.REFUNDED)
    service = ConsultationRefundService(_SequentialDb(payment, _case()))
    service._latest_payment_history_event = AsyncMock(
        return_value=_audit_event(
            decision="refunded",
            actor_id=11,
            comment="provider operation 12345",
        )
    )

    with pytest.raises(RefundResolutionConflictError):
        asyncio.run(
            service.resolve_refund(
                payment_id=payment.id,
                decision="refunded",
                actor_id=12,
                comment="provider operation 12345",
            )
        )


def test_opposite_stale_decision_after_refund_is_a_conflict():
    payment = _payment(status=PaymentStatus.REFUNDED)
    service = ConsultationRefundService(_SequentialDb(payment, _case()))

    with pytest.raises(RefundResolutionConflictError):
        asyncio.run(
            service.resolve_refund(
                payment_id=payment.id,
                decision="declined",
                actor_id=11,
                comment="old browser tab rejection",
            )
        )


def test_exact_retry_of_reopened_refund_is_accepted_without_second_transition():
    payment = _payment(status=PaymentStatus.REFUND_PENDING)
    service = ConsultationRefundService(_SequentialDb(payment, _case()))
    service._latest_payment_history_event = AsyncMock(
        return_value=_audit_event(
            status=PaymentStatus.REFUND_PENDING.value,
            actor_id=21,
            comment="provider issue fixed and checked",
        )
    )

    result, case = asyncio.run(
        service.reopen_declined_refund(
            payment_id=payment.id,
            actor_id=21,
            comment="provider issue fixed and checked",
        )
    )

    assert result is payment
    assert case.id == 801
    assert payment.status == PaymentStatus.REFUND_PENDING
    service._latest_payment_history_event.assert_awaited_once()


def test_already_reopened_refund_from_other_admin_is_a_conflict():
    payment = _payment(status=PaymentStatus.REFUND_PENDING)
    service = ConsultationRefundService(_SequentialDb(payment, _case()))
    service._latest_payment_history_event = AsyncMock(
        return_value=_audit_event(
            status=PaymentStatus.REFUND_PENDING.value,
            actor_id=21,
            comment="provider issue fixed and checked",
        )
    )

    with pytest.raises(RefundResolutionConflictError):
        asyncio.run(
            service.reopen_declined_refund(
                payment_id=payment.id,
                actor_id=22,
                comment="provider issue fixed and checked",
            )
        )
