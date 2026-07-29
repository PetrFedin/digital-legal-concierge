from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bot.screens import consultation_payment
from app.domain.statuses.case_statuses import RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus


class FakeMessage:
    def __init__(self):
        self.texts: list[str] = []
        self.markups = []

    async def edit_text(self, text, **kwargs):
        self.texts.append(text)
        self.markups.append(kwargs.get("reply_markup"))


class FakeCallback:
    def __init__(self):
        self.from_user = SimpleNamespace(id=990001)
        self.data = "consult_pay"
        self.message = FakeMessage()


class FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_consultation_payment_uses_only_single_active_m2_case(monkeypatch):
    user = SimpleNamespace(id=77, is_blocked=False)
    m2_case = SimpleNamespace(id=202, client_id=user.id, route=RouteCode.M2.value)
    calls = SimpleNamespace(route=None, prepared_case=None, payment_case=None)

    class FakeCaseService:
        async def list_active_cases_for_user(self, user_id, *, route=None):
            assert user_id == user.id
            calls.route = route
            return [m2_case]

    class FakeContext:
        def __init__(self, db):
            self.case_service = FakeCaseService()

        async def get_user_from_callback(self, callback):
            return user

    class FakeLifecycle:
        def __init__(self, db):
            pass

        async def prepare_payment(self, *, case, client_id, **kwargs):
            assert client_id == user.id
            calls.prepared_case = case

    payment = SimpleNamespace(
        id=501,
        case_id=m2_case.id,
        amount=Decimal("5000.00"),
        status=PaymentStatus.WAITING_CONFIRMATION.value,
        payment_url="https://pay.example.test/m2",
        manual_review_required=False,
    )

    class FakePayments:
        def __init__(self, db):
            pass

        async def get_or_create_payment(self, *, case, payment_code):
            calls.payment_case = case
            return payment

        async def create_payment_link(self, current):
            return current

    monkeypatch.setattr(consultation_payment, "BotContextService", FakeContext)
    monkeypatch.setattr(
        consultation_payment,
        "ConsultationPaymentLifecycleService",
        FakeLifecycle,
    )
    monkeypatch.setattr(consultation_payment, "PaymentService", FakePayments)

    callback = FakeCallback()
    db = FakeDb()
    await consultation_payment.consultation_payment(callback, db)

    assert calls.route == RouteCode.M2
    assert calls.prepared_case is m2_case
    assert calls.payment_case is m2_case
    assert db.commits == 1
    assert db.rollbacks == 0
    assert "5 000.00 ₽" in callback.message.texts[-1]
    assert "202" not in callback.message.texts[-1]
    assert "501" not in callback.message.texts[-1]


@pytest.mark.asyncio
async def test_multiple_active_m2_cases_stop_payment(monkeypatch):
    user = SimpleNamespace(id=78, is_blocked=False)

    class FakeCaseService:
        async def list_active_cases_for_user(self, user_id, *, route=None):
            assert route == RouteCode.M2
            return [
                SimpleNamespace(id=301, client_id=user.id),
                SimpleNamespace(id=302, client_id=user.id),
            ]

    class FakeContext:
        def __init__(self, db):
            self.case_service = FakeCaseService()

        async def get_user_from_callback(self, callback):
            return user

    class ForbiddenPaymentService:
        def __init__(self, db):
            raise AssertionError("PaymentService must not be created")

    monkeypatch.setattr(consultation_payment, "BotContextService", FakeContext)
    monkeypatch.setattr(
        consultation_payment,
        "PaymentService",
        ForbiddenPaymentService,
    )

    callback = FakeCallback()
    db = FakeDb()
    await consultation_payment.consultation_payment(callback, db)

    assert db.commits == 0
    assert db.rollbacks == 1
    assert "однозначно определить" in callback.message.texts[-1]
    assert "повторно" in callback.message.texts[-1].lower()


def test_m2_payment_router_precedes_general_payment_router():
    entry = Path("app/bot/screens/consultation_entry.py").read_text(encoding="utf-8")
    bot = Path("app/bot/bot.py").read_text(encoding="utf-8")

    assert entry.index("include_router(payment_router)") < entry.index(
        "include_router(reservation_router)"
    )
    assert bot.index("consultation_entry.router") < bot.index("payments.router")
