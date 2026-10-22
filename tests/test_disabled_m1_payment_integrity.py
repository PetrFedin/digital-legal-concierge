"""Disabled providers must not bypass M1 legal or financial milestones.

Exercise the registered Telegram routers in their production order against an
isolated database. Telegram presentation is captured; no external API is used.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens import m1_stages, no_payment, no_payment_legal, payments
from app.config import settings
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User

CLIENT_TELEGRAM_ID = 990220041


class FakeMessage:
    def __init__(self):
        self.edits = []
        self.answers = []

    async def edit_text(self, text, *, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def answer(self, text, *, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.from_user = SimpleNamespace(
            id=CLIENT_TELEGRAM_ID,
            username="disabled_m1_test",
            full_name="Synthetic M1 client",
        )
        self.message = FakeMessage()
        self.acks = []

    async def answer(self, text=None, show_alert=False):
        self.acks.append((text, show_alert))


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "disabled")
    provider_calls = []

    async def forbidden_payment_link(*_args, **_kwargs):
        provider_calls.append(True)
        raise AssertionError("A disabled M1 action must not create a provider link")

    monkeypatch.setattr(PaymentService, "create_payment_link", forbidden_payment_link)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'disabled_m1.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()
        assert not provider_calls, "Disabled M1 tried to invoke the payment provider"


async def add_case(db, status, *, route=RouteCode.M1):
    user = User(
        telegram_id=CLIENT_TELEGRAM_ID,
        telegram_username="disabled_m1_test",
        full_name="Synthetic M1 client",
    )
    db.add(user)
    await db.flush()
    case = Case(
        case_number="SYNTHETIC-DISABLED-M1",
        client_id=user.id,
        route=route,
        status=status,
    )
    db.add(case)
    await db.flush()
    user.active_case_id = case.id
    await db.commit()
    return case


async def dispatch_callback(db, data):
    callback = FakeCallback(data)
    # This is the relative order used by build_dispatcher. In particular, the
    # disabled routers intercept these callbacks before the canonical routers.
    for router in (
        no_payment_legal.router,
        no_payment.router,
        payments.router,
        m1_stages.router,
    ):
        result = await router.propagate_event(
            update_type="callback_query", event=callback, db=db
        )
        if result is not UNHANDLED:
            assert callback.message.edits or callback.message.answers or callback.acks
            return callback
    pytest.fail(f"No registered handler accepted {data}")


async def assert_no_payment_or_transition(db, case, expected_status):
    await db.refresh(case)
    assert case.status == expected_status
    assert case.closed_at is None
    assert case.enforcement_started_at is None
    assert await db.scalar(select(func.count(Payment.id))) == 0
    assert await db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "CASE_STATUS_CHANGED"
        )
    ) == 0


@pytest.mark.asyncio
async def test_disabled_legacy_contract_button_does_not_acknowledge_or_pay(db):
    case = await add_case(db, CaseStatus.M1_CONTRACT_READY)

    await dispatch_callback(db, "contract_sign")

    await assert_no_payment_or_transition(db, case, CaseStatus.M1_CONTRACT_READY)
    assert case.contract_signed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [CaseStatus.M1_CONTRACT_READY, CaseStatus.M1_WAITING_PAYMENT_30000]
)
async def test_disabled_initial_payment_neither_signs_nor_skips_payment(db, status):
    case = await add_case(db, status)
    if status == CaseStatus.M1_WAITING_PAYMENT_30000:
        case.contract_signed_at = datetime.now(timezone.utc)
        await db.commit()

    await dispatch_callback(db, "pay_start_30000")

    await assert_no_payment_or_transition(db, case, status)
    assert case.poa_instruction_sent_at is None
    assert case.poa_received_at is None
    if status == CaseStatus.M1_CONTRACT_READY:
        assert case.contract_signed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.M1_WAITING_30_DAYS,
        CaseStatus.M1_COURT_STAGE,
        CaseStatus.M1_WAITING_PAYMENT_70000,
    ],
)
async def test_disabled_court_payment_does_not_open_enforcement(db, status):
    case = await add_case(db, status)
    if status == CaseStatus.M1_WAITING_PAYMENT_70000:
        case.decision_date = datetime.now(timezone.utc)
        await db.commit()

    await dispatch_callback(db, "pay_court_70000")

    await assert_no_payment_or_transition(db, case, status)


@pytest.mark.asyncio
async def test_disabled_success_fee_stays_pending_and_is_not_closed_on_repeat(db):
    case = await add_case(db, CaseStatus.M1_MONEY_RECEIVED)
    case.received_amount = Decimal("500000.00")
    case.received_at = datetime.now(timezone.utc)
    await db.commit()

    for _ in range(2):
        await dispatch_callback(db, "pay_success_fee")
        await db.refresh(case)
        assert case.status == CaseStatus.M1_WAITING_SUCCESS_FEE
        assert case.closed_at is None
        assert case.received_amount == Decimal("500000.00")
        assert case.success_fee_amount == Decimal("50000.00")
        payment = (await db.scalars(select(Payment))).one()
        assert payment.payment_code == PaymentCode.M1_SUCCESS_FEE
        assert payment.amount == Decimal("50000.00")
        assert payment.status == PaymentStatus.PENDING
        assert payment.payment_url is None
        assert payment.provider_payment_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected_text",
    [
        (CaseStatus.M1_WAITING_30_DAYS, "контрольный срок"),
        (CaseStatus.M1_LAWSUIT_PREPARATION, "готовит иск"),
        (CaseStatus.M1_LAWSUIT_FILED, "Иск подан"),
        (CaseStatus.M1_COURT_STAGE, "Судебный этап открыт"),
        (CaseStatus.M1_DECISION_RECEIVED, "Решение суда получено"),
        (CaseStatus.M1_WAITING_PAYMENT_70000, "Онлайн-оплата сейчас отключена"),
    ],
)
async def test_disabled_court_screen_shows_current_milestone_without_transition(
    db, status, expected_text
):
    case = await add_case(db, status)
    case.claim_waiting_until = datetime.now(timezone.utc) + timedelta(days=10)
    await db.commit()

    callback = await dispatch_callback(db, "court_status")

    await assert_no_payment_or_transition(db, case, status)
    text, markup = callback.message.edits[-1]
    assert expected_text in text
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert all(button.callback_data != "pay_court_70000" for button in buttons)
    assert all(button.url is None for button in buttons)


@pytest.mark.asyncio
async def test_disabled_m1_payments_screen_keeps_pending_obligation_visible(db):
    case = await add_case(db, CaseStatus.M1_WAITING_PAYMENT_30000)
    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M1_INITIAL_PAYMENT,
        title="Первый платеж М1",
        amount=Decimal("30000.00"),
        status=PaymentStatus.PENDING,
    )
    db.add(payment)
    await db.commit()

    callback = await dispatch_callback(db, "payments_open")

    text, _markup = callback.message.edits[-1]
    assert "Первый платеж М1" in text
    assert "30 000,00 ₽" in text
    assert "Ожидает оплаты" in text
    await db.refresh(case)
    await db.refresh(payment)
    assert case.status == CaseStatus.M1_WAITING_PAYMENT_30000
    assert payment.status == PaymentStatus.PENDING
    assert payment.payment_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data", ["contract_sign", "pay_start_30000", "pay_court_70000", "pay_success_fee"]
)
async def test_disabled_m1_callbacks_cannot_change_active_m2_case(db, data):
    case = await add_case(db, CaseStatus.M2_SLOT_PENDING, route=RouteCode.M2)

    await dispatch_callback(db, data)

    await assert_no_payment_or_transition(db, case, CaseStatus.M2_SLOT_PENDING)
    assert case.route == RouteCode.M2
    assert case.contract_signed_at is None
