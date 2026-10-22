"""Durable client milestones, replay safety and the client-to-lawyer POA handoff.

Only Telegram presentation and transport are fake. The handlers, status policy,
payment ledger, messages, audit and notification queue use an isolated database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot import client_case_view
from app.bot.client_case_view import load_client_case_view
from app.bot.screens import m1_stages, my_case
from app.domain.cases.m1_process_service import M1ProcessService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.message import Message
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User


TELEGRAM_ID = 998226001


class FakeMessage:
    def __init__(self, *, stale=False):
        self.stale = stale
        self.edits = []
        self.answers = []

    async def edit_text(self, text, *, reply_markup=None):
        if self.stale:
            raise TelegramBadRequest(method=None, message="message can't be edited")
        self.edits.append((text, reply_markup))

    async def answer(self, text, *, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data, *, user_id=TELEGRAM_ID, stale=False):
        self.data = data
        self.from_user = SimpleNamespace(
            id=user_id, username="milestone_client", full_name="Клиент тестового дела",
        )
        self.message = FakeMessage(stale=stale)
        self.acks = []

    async def answer(self, text=None, show_alert=False):
        self.acks.append((text, show_alert))


def buttons(callback):
    rendered = callback.message.edits + callback.message.answers
    return [
        button.callback_data
        for _, markup in rendered if markup
        for row in markup.inline_keyboard
        for button in row
    ]


@pytest.fixture
async def milestone_context(tmp_path, monkeypatch):
    # No external payment provider is required to record the contract milestone.
    monkeypatch.setattr(m1_stages, "payments_disabled", lambda: True)
    monkeypatch.setattr(client_case_view, "payments_disabled", lambda: True)
    monkeypatch.setattr(my_case, "payments_disabled", lambda: True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'milestones.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        user = User(telegram_id=TELEGRAM_ID, full_name="Клиент тестового дела")
        lawyer = Lawyer(
            full_name="Юрист тестового дела", email="milestones@example.test",
            telegram_id=998226002,
        )
        db.add_all([user, lawyer])
        await db.flush()
        case = Case(
            case_number="MILESTONE-2026-1", client_id=user.id, route="M1",
            status=CaseStatus.M1_CONTRACT_READY, assigned_lawyer_id=lawyer.id,
        )
        db.add(case)
        await db.flush()
        user.active_case_id = case.id
        await db.commit()
        context = SimpleNamespace(
            factory=factory, case_id=case.id, user_id=user.id, lawyer_id=lawyer.id,
        )
    try:
        yield context
    finally:
        await engine.dispose()


async def prepare_poa(context, payment_status=PaymentStatus.PAID):
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        case.status = CaseStatus.M1_POWER_OF_ATTORNEY
        case.contract_signed_at = datetime.now(timezone.utc)
        if payment_status is not None:
            db.add(Payment(
                case_id=case.id, payment_code=PaymentCode.M1_INITIAL_PAYMENT,
                title="Первый платёж", amount=Decimal("30000.00"),
                status=payment_status,
            ))
        await db.commit()


async def count(db, model, *conditions):
    return await db.scalar(select(func.count(model.id)).where(*conditions))


async def test_contract_payment_poa_report_and_lawyer_receipt_survive_reentry(
    milestone_context,
):
    context = milestone_context
    async with context.factory() as db:
        opened = FakeCallback("contract_open")
        await m1_stages.contract_open(opened, db)
        assert f"contract_sign:{context.case_id}" in buttons(opened)
        assert "MILESTONE-2026-1" in opened.message.edits[0][0]

        signed = FakeCallback(f"contract_sign:{context.case_id}", stale=True)
        await m1_stages.contract_sign(signed, db)
        assert "Подтверждение договора сохранено" in signed.message.answers[0][0]
        assert "pay_start_30000" not in buttons(signed)

    # Re-enter with a fresh session, like returning to the bot on another day.
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        assert case.status == CaseStatus.M1_WAITING_PAYMENT_30000
        signed_at = case.contract_signed_at
        assert signed_at is not None
        assert case.poa_received_at is None
        payment = (await db.execute(select(Payment))).scalar_one()
        assert payment.status == PaymentStatus.PENDING
        assert payment.amount == Decimal("30000.00")
        view = await load_client_case_view(db, case)
        assert view.payments_summary == "Ожидают подтверждения: 1"
        assert view.action.callback == "payments_open"
        assert "ожидает подтверждения команды" in view.next_action
        assert ("💳 Оплаты", "payments_open") in my_case._case_buttons(view)

        await m1_stages.contract_sign(FakeCallback(f"contract_sign:{case.id}"), db)
        await db.refresh(case)
        assert case.contract_signed_at == signed_at
        assert await count(db, Payment) == 1

        # Simulate a verified provider result through the real payment service.
        await PaymentWebhookService(db).process_successful_payment(
            payment=payment, case=case, provider_payload={"synthetic": True},
        )
        await db.commit()

    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        assert case.status == CaseStatus.M1_POWER_OF_ATTORNEY
        instruction = FakeCallback("poa_instruction")
        await m1_stages.poa_instruction(instruction, db)
        assert f"poa_done:{case.id}" in buttons(instruction)
        assert case.poa_instruction_sent_at is not None

        reported = FakeCallback(f"poa_done:{case.id}", stale=True)
        await m1_stages.poa_done(reported, db)
        assert "сохранена для команды" in reported.message.answers[0][0]

    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        reported_at = case.poa_client_confirmed_at
        assert reported_at is not None
        assert case.status == CaseStatus.M1_POWER_OF_ATTORNEY
        assert case.poa_received_at is None
        assert case.claim_sent_at is None
        assert case.claim_waiting_until is None
        assert await count(db, Message) == 1
        report = (await db.execute(select(Message))).scalar_one()
        assert report.sender_id == context.user_id
        assert report.sender_type == "client"
        assert "передаче оформленной доверенности" in report.text
        assert await count(db, AuditLog, AuditLog.action == "POA_READY_REPORTED") == 1
        notices = list((await db.execute(select(Notification).where(
            Notification.event_code == "CLIENT_MESSAGE_RECEIVED",
        ))).scalars())
        assert {notice.recipient_type for notice in notices} == {"admin", "lawyer"}
        notification_count = len(notices)

        view = await load_client_case_view(db, case)
        assert view.status_label == "Доверенность ожидает проверки команды"
        assert view.action.label == "Статус доверенности"
        assert "Ожидаем проверки" in view.next_action

        # Replaying either supported callback spelling is idempotent.
        await m1_stages.poa_done(FakeCallback(f"poa_done:{case.id}"), db)
        await m1_stages.poa_done(FakeCallback(f"poa_sent_confirm:{case.id}"), db)
        await db.refresh(case)
        assert case.poa_client_confirmed_at == reported_at
        assert await count(db, Message) == 1
        assert await count(db, AuditLog, AuditLog.action == "POA_READY_REPORTED") == 1
        assert await count(db, Notification,
                           Notification.event_code == "CLIENT_MESSAGE_RECEIVED") == notification_count
        instruction = FakeCallback("poa_instruction")
        await m1_stages.poa_instruction(instruction, db)
        assert f"poa_done:{case.id}" not in buttons(instruction)
        assert "Ожидаем проверки" in instruction.message.edits[0][0]

        service = M1ProcessService(db)
        await service.mark_poa_received(
            case=case, lawyer_id=context.lawyer_id,
            comment="Доверенность и нотариальные копии получены курьером",
        )
        await db.commit()

    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        assert case.status == CaseStatus.M1_POA_RECEIVED
        assert case.poa_received_at is not None
        assert case.poa_client_confirmed_at == reported_at
        notice = (await db.execute(select(Notification).where(
            Notification.event_code == "M1_POA_RECEIVED",
        ))).scalar_one()
        assert notice.recipient_type == "client"
        assert notice.target_chat_id == TELEGRAM_ID
        assert notice.status == "PENDING"
        assert "Следующий шаг — подготовка претензии" in notice.text
        assert "курьером" not in notice.text
        await M1ProcessService(db).mark_claim_sent(
            case=case, lawyer_id=context.lawyer_id,
            comment="Претензия фактически направлена застройщику",
        )
        await db.commit()
        assert case.status == CaseStatus.M1_WAITING_30_DAYS
        assert case.claim_waiting_until - case.claim_sent_at == timedelta(days=30)
        stale = FakeCallback(f"poa_done:{case.id}")
        await m1_stages.poa_done(stale, db)
        assert case.status == CaseStatus.M1_WAITING_30_DAYS
        assert await count(db, Message) == 1
        assert "уже подтверждена командой" in stale.message.edits[0][0]


@pytest.mark.parametrize("action", ["contract_sign", "poa_done", "poa_sent_confirm"])
@pytest.mark.parametrize("variant", ["unscoped", "malformed", "switched", "foreign"])
async def test_stale_or_foreign_buttons_never_mutate_another_case(
    milestone_context, action, variant,
):
    context = milestone_context
    is_poa = action != "contract_sign"
    if is_poa:
        await prepare_poa(context)
    async with context.factory() as db:
        original = await db.get(Case, context.case_id)
        client = await db.get(User, context.user_id)
        other_user = User(telegram_id=TELEGRAM_ID + 10)
        db.add(other_user)
        await db.flush()
        other = Case(
            case_number="MILESTONE-OTHER", client_id=(
                other_user.id if variant == "foreign" else client.id
            ), route="M1", status=original.status,
        )
        db.add(other)
        await db.flush()
        if variant == "switched":
            client.active_case_id = other.id
        await db.commit()
        expected_status = original.status
        before_payment_count = await count(db, Payment)
        data = {
            "unscoped": action,
            "malformed": f"{action}:{context.case_id}:extra",
            "switched": f"{action}:{context.case_id}",
            "foreign": f"{action}:{other.id}",
        }[variant]
        handler = m1_stages.poa_done if is_poa else m1_stages.contract_sign
        callback = FakeCallback(data)
        await handler(callback, db)
        assert "Дело не изменено" in callback.message.edits[0][0]
        await db.commit()
    async with context.factory() as db:
        for case in (await db.execute(select(Case))).scalars():
            assert case.status == expected_status
            assert case.poa_client_confirmed_at is None
            assert case.poa_received_at is None
            if not is_poa:
                assert case.contract_signed_at is None
        assert await count(db, Message) == 0
        assert await count(db, AuditLog) == 0
        assert await count(db, Payment) == before_payment_count


@pytest.mark.parametrize("payment_status", [
    None, PaymentStatus.PENDING, PaymentStatus.PAID_REVIEW, PaymentStatus.REFUNDED,
])
async def test_poa_instruction_and_report_require_verified_initial_payment(
    milestone_context, payment_status,
):
    context = milestone_context
    await prepare_poa(context, payment_status)
    async with context.factory() as db:
        instruction = FakeCallback("poa_instruction")
        await m1_stages.poa_instruction(instruction, db)
        assert "подтверждения первого платежа" in instruction.message.edits[0][0]
        reported = FakeCallback(f"poa_done:{context.case_id}")
        await m1_stages.poa_done(reported, db)
        assert "подтверждение первого платежа" in reported.message.edits[0][0]
        await db.commit()
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        assert case.status == CaseStatus.M1_POWER_OF_ATTORNEY
        assert case.poa_instruction_sent_at is None
        assert case.poa_client_confirmed_at is None
        assert case.poa_received_at is None
        assert await count(db, Message) == 0
        assert await count(db, AuditLog) == 0


@pytest.mark.parametrize("restriction", ["blocked", "erased", "closed", "early_stage"])
async def test_client_cannot_report_poa_when_case_is_unavailable(
    milestone_context, restriction,
):
    context = milestone_context
    await prepare_poa(context)
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        if restriction == "blocked":
            (await db.get(User, context.user_id)).is_blocked = True
        elif restriction == "erased":
            case.content_deleted_at = datetime.now(timezone.utc)
        elif restriction == "closed":
            case.status = CaseStatus.M1_CLOSED
        else:
            case.status = CaseStatus.M1_CONTRACT_READY
        await db.commit()
        expected_status = case.status
        await m1_stages.poa_done(FakeCallback(f"poa_done:{case.id}"), db)
        await db.commit()
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        assert case.status == expected_status
        assert case.poa_client_confirmed_at is None
        assert case.poa_received_at is None
        assert await count(db, Message) == 0
        assert await count(db, AuditLog) == 0


async def test_poa_report_is_atomic_when_notification_queue_fails(
    milestone_context, monkeypatch,
):
    context = milestone_context
    await prepare_poa(context)

    async def fail_queue(*args, **kwargs):
        raise RuntimeError("synthetic queue failure")

    monkeypatch.setattr(m1_stages.NotificationEngine, "emit", fail_queue)
    async with context.factory() as db:
        callback = FakeCallback(f"poa_done:{context.case_id}")
        await m1_stages.poa_done(callback, db)
        assert "пока не сохранено" in callback.message.edits[0][0]
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        assert case.status == CaseStatus.M1_POWER_OF_ATTORNEY
        assert case.poa_client_confirmed_at is None
        assert case.poa_received_at is None
        assert await count(db, Message) == 0
        assert await count(db, AuditLog) == 0
        assert await count(db, Notification) == 0


async def test_contract_stamp_and_status_roll_back_if_invoice_cannot_be_saved(
    milestone_context, monkeypatch,
):
    context = milestone_context

    async def fail_invoice(*args, **kwargs):
        raise RuntimeError("synthetic invoice failure")

    monkeypatch.setattr(m1_stages.PaymentService, "get_or_create_payment", fail_invoice)
    async with context.factory() as db:
        callback = FakeCallback(f"contract_sign:{context.case_id}")
        await m1_stages.contract_sign(callback, db)
        assert "пока не сохранено" in callback.message.edits[0][0]
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        assert case.status == CaseStatus.M1_CONTRACT_READY
        assert case.contract_signed_at is None
        assert await count(db, Payment) == 0
        assert await count(db, AuditLog) == 0
