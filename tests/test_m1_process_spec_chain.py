from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.domain.cases.m1_process_service import M1ProcessError, M1ProcessService
from app.domain.payments import payment_webhook_service
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus


class FakeDB:
    async def flush(self):
        return None


class FakeCases:
    def __init__(self):
        self.transitions: list[tuple[CaseStatus, str]] = []

    async def change_status(self, *, case, next_status, comment, **_kwargs):
        case.status = next_status
        self.transitions.append((CaseStatus(next_status), comment))
        return case


class FakePayments:
    def __init__(self):
        self.created: list[str] = []

    async def get_or_create_payment(self, *, case, payment_code):
        self.created.append(str(payment_code))
        return SimpleNamespace(amount="70000.00")


class FakeNotifications:
    def __init__(self):
        self.events: list[str] = []

    async def emit(self, *, event_code, **_kwargs):
        self.events.append(event_code)
        return SimpleNamespace(id=len(self.events))


def service_with_fakes():
    service = M1ProcessService(FakeDB())
    service.cases = FakeCases()
    service.payments = FakePayments()
    service.notifications = FakeNotifications()
    return service


@pytest.mark.asyncio
async def test_claim_action_opens_explicit_thirty_day_wait():
    service = service_with_fakes()
    case = SimpleNamespace(
        id=1,
        case_number="DLC-1",
        status=CaseStatus.M1_POA_RECEIVED,
    )

    await service.mark_claim_sent(
        case=case,
        lawyer_id=7,
        comment="Претензия направлена застройщику",
    )

    assert [status for status, _ in service.cases.transitions] == [
        CaseStatus.M1_CLAIM_PREPARATION,
        CaseStatus.M1_CLAIM_SENT,
        CaseStatus.M1_WAITING_30_DAYS,
    ]
    assert case.status == CaseStatus.M1_WAITING_30_DAYS


@pytest.mark.asyncio
async def test_court_decision_is_required_before_second_payment_stage():
    service = service_with_fakes()
    case = SimpleNamespace(
        id=2,
        case_number="DLC-2",
        status=CaseStatus.M1_COURT_STAGE,
    )

    await service.record_court_decision(
        case=case,
        lawyer_id=7,
        comment="Получено решение суда в пользу клиента",
    )

    assert [status for status, _ in service.cases.transitions] == [
        CaseStatus.M1_DECISION_RECEIVED,
        CaseStatus.M1_WAITING_PAYMENT_70000,
    ]
    assert service.payments.created == [str(PaymentCode.M1_COURT_PAYMENT)]
    assert service.notifications.events == ["M1_COURT_DECISION_RECEIVED"]


@pytest.mark.asyncio
async def test_final_close_requires_success_fee_and_persists_reason():
    service = service_with_fakes()
    case = SimpleNamespace(
        id=3,
        case_number="DLC-3",
        status=CaseStatus.M1_SUCCESS_FEE_RECEIVED,
        closure_reason=None,
    )

    await service.close_after_success_fee(
        case=case,
        actor_type="lawyer",
        actor_id=7,
        reason="Финальный платёж подтверждён, обязательства завершены",
    )

    assert case.status == CaseStatus.M1_CLOSED
    assert "обязательства завершены" in case.closure_reason
    assert service.notifications.events == ["M1_CASE_CLOSED"]


@pytest.mark.asyncio
async def test_final_close_fails_before_success_fee():
    service = service_with_fakes()
    case = SimpleNamespace(
        id=4,
        case_number="DLC-4",
        status=CaseStatus.M1_WAITING_SUCCESS_FEE,
        closure_reason=None,
    )

    with pytest.raises(M1ProcessError, match="недоступно"):
        await service.close_after_success_fee(
            case=case,
            actor_type="lawyer",
            actor_id=7,
            reason="Попытка закрытия до подтверждения финального платежа",
        )

    assert case.status == CaseStatus.M1_WAITING_SUCCESS_FEE
    assert case.closure_reason is None


def test_success_fee_webhook_does_not_auto_close_case():
    source = inspect.getsource(
        payment_webhook_service.PaymentWebhookService.process_successful_payment
    )
    mapping = source.split("mapping = {", 1)[1].split("}", 1)[0]

    success_fee = mapping.split("PaymentCode.M1_SUCCESS_FEE:", 1)[1]
    assert "CaseStatus.M1_SUCCESS_FEE_RECEIVED" in success_fee
    assert "CaseStatus.M1_CLOSED" not in success_fee
