from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.screens import payments
from app.domain.consultations.consultation_intake import ConsultationDescriptionRequired
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus


PAYMENT_CONTEXTS = [
    (
        PaymentCode.M1_INITIAL_PAYMENT,
        CaseStatus.M1_WAITING_PAYMENT_30000,
        Decimal("30000"),
        "инструкция по доверенности",
        "pay_start_30000",
    ),
    (
        PaymentCode.M1_COURT_PAYMENT,
        CaseStatus.M1_WAITING_PAYMENT_70000,
        Decimal("70000"),
        "исполнительный этап",
        "pay_court_70000",
    ),
    (
        PaymentCode.M1_SUCCESS_FEE,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
        Decimal("15000"),
        "финансовый этап",
        "pay_success_fee",
    ),
    (
        PaymentCode.M2_CONSULTATION_PAYMENT,
        CaseStatus.M2_PAYMENT_PENDING,
        Decimal("5000"),
        "слот станет окончательно вашим",
        "consult_pay",
    ),
]


def callback_values(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.fixture
def scenario(monkeypatch):
    def build(code, *, disabled=False, active=True, edit_error=None):
        _, status, amount, _, _ = next(row for row in PAYMENT_CONTEXTS if row[0] == code)
        events = []
        case = SimpleNamespace(
            id=42,
            client_id=7,
            route="M2" if code == PaymentCode.M2_CONSULTATION_PAYMENT else "M1",
            status=status,
            contract_signed_at=datetime.now(timezone.utc),
            decision_date=datetime.now(timezone.utc),
            received_at=datetime.now(timezone.utc),
            received_amount=Decimal("150000"),
            content_deleted_at=None,
        )
        payment = SimpleNamespace(
            id=81,
            case_id=case.id,
            payment_code=code,
            title="Платёж по договору",
            amount=amount,
            payment_url="https://payments.example.test/pay/81",
            status=PaymentStatus.WAITING_CONFIRMATION,
        )

        async def create(**kwargs):
            events.append("create")
            return payment

        async def link(value):
            events.append("link")
            return value

        async def commit():
            events.append("commit")

        async def edit(text, *, reply_markup):
            events.append("edit")
            if edit_error:
                raise edit_error

        async def answer(text, *, reply_markup):
            events.append("answer")

        db = SimpleNamespace(commit=AsyncMock(side_effect=commit), rollback=AsyncMock())
        service = SimpleNamespace(
            get_or_create_payment=AsyncMock(side_effect=create),
            create_payment_link=AsyncMock(side_effect=link),
        )
        service_factory = Mock(return_value=service)
        context = SimpleNamespace(
            get_user_from_callback=AsyncMock(return_value=SimpleNamespace(id=case.client_id)),
            case_service=SimpleNamespace(
                get_active_case_for_user=AsyncMock(return_value=case if active else None)
            ),
        )
        callback = SimpleNamespace(
            data="pay_start_30000",
            message=SimpleNamespace(
                edit_text=AsyncMock(side_effect=edit),
                answer=AsyncMock(side_effect=answer),
            ),
            answer=AsyncMock(),
        )
        monkeypatch.setattr(payments, "BotContextService", lambda _db: context)
        monkeypatch.setattr(payments, "PaymentService", service_factory)
        monkeypatch.setattr(payments, "payments_disabled", lambda: disabled)
        monkeypatch.setattr(payments, "fake_payments_enabled", lambda: False)
        return SimpleNamespace(
            case=case,
            payment=payment,
            callback=callback,
            db=db,
            service=service,
            service_factory=service_factory,
            events=events,
        )

    return build


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status,amount,next_step,retry", PAYMENT_CONTEXTS)
async def test_payment_link_explains_its_own_next_step_after_commit(
    scenario, code, status, amount, next_step, retry
):
    flow = scenario(code)

    await payments.start_payment(flow.callback, flow.db, code)

    text = flow.callback.message.edit_text.await_args.args[0]
    markup = flow.callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert next_step in text
    assert payments.money(amount) in text
    if code != PaymentCode.M2_CONSULTATION_PAYMENT:
        assert "слот" not in text.lower()
        assert "выбранное время" not in text.lower()
        assert "консультаци" not in text.lower()
    else:
        assert "Не используйте эту ссылку после выбора другого времени." in text
    assert markup.inline_keyboard[0][0].url == flow.payment.payment_url
    assert flow.events == ["create", "link", "commit", "edit"]
    assert flow.case.status == status
    flow.service.get_or_create_payment.assert_awaited_once_with(
        case=flow.case, payment_code=code
    )
    flow.db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status,amount,next_step,retry", PAYMENT_CONTEXTS)
async def test_payment_provider_failure_retries_the_same_payment_route(
    scenario, code, status, amount, next_step, retry
):
    flow = scenario(code)
    flow.service.create_payment_link.side_effect = RuntimeError("Provider unavailable")

    await payments.start_payment(flow.callback, flow.db, code)

    text = flow.callback.message.edit_text.await_args.args[0]
    markup = flow.callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert callback_values(markup) == [retry, "my_case_open", "nav_home"]
    assert "Платёжный сервис временно недоступен" in text
    if code != PaymentCode.M2_CONSULTATION_PAYMENT:
        assert "выбранное время" not in text.lower()
        assert "следующий этап не открыт" in text
    else:
        assert "Выбранное время, вопрос и документы сохранены" in text
    assert flow.case.status == status
    flow.db.rollback.assert_awaited_once()
    flow.db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,changes,expected_text",
    [
        (PaymentCode.M1_INITIAL_PAYMENT, {"status": CaseStatus.M1_CONTRACT_READY}, "текущему этапу"),
        (PaymentCode.M1_INITIAL_PAYMENT, {"contract_signed_at": None}, "подписания договора"),
        (PaymentCode.M1_INITIAL_PAYMENT, {"status": CaseStatus.M1_PAYMENT_30000_RECEIVED}, "текущему этапу"),
        (PaymentCode.M1_COURT_PAYMENT, {"status": CaseStatus.M1_COURT_STAGE}, "текущему этапу"),
        (PaymentCode.M1_COURT_PAYMENT, {"decision_date": None}, "решения суда"),
        (PaymentCode.M1_SUCCESS_FEE, {"status": CaseStatus.M1_ENFORCEMENT}, "текущему этапу"),
        (PaymentCode.M1_SUCCESS_FEE, {"received_at": None}, "получения денег"),
        (PaymentCode.M1_SUCCESS_FEE, {"received_amount": Decimal("0")}, "суммы поступления"),
        (PaymentCode.M1_SUCCESS_FEE, {"received_amount": Decimal("-1")}, "суммы поступления"),
        (PaymentCode.M1_SUCCESS_FEE, {"received_amount": None}, "суммы поступления"),
        (PaymentCode.M1_INITIAL_PAYMENT, {"route": "M2"}, "текущему этапу"),
        (PaymentCode.M1_INITIAL_PAYMENT, {"content_deleted_at": datetime.now(timezone.utc)}, "текущему этапу"),
        (PaymentCode.M1_INITIAL_PAYMENT, {"status": CaseStatus.ARCHIVED}, "текущему этапу"),
    ],
)
async def test_m1_forbidden_or_stale_payment_never_reaches_payment_service(
    scenario, code, changes, expected_text
):
    flow = scenario(code)
    for field, value in changes.items():
        setattr(flow.case, field, value)
    original_status = flow.case.status

    await payments.start_payment(flow.callback, flow.db, code)

    text = flow.callback.message.edit_text.await_args.args[0]
    markup = flow.callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert expected_text in text
    assert "Новый платёж не создавался" in text
    assert "my_case_open" in callback_values(markup)
    assert flow.case.status == original_status
    flow.service_factory.assert_not_called()
    flow.db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", list(payments.M1_PAYMENT_CODES))
async def test_m1_without_an_active_case_returns_to_case_without_creating_payment(scenario, code):
    flow = scenario(code, active=False)

    await payments.start_payment(flow.callback, flow.db, code)

    text = flow.callback.message.edit_text.await_args.args[0]
    markup = flow.callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert "Активное дело не найдено" in text
    assert "консультаци" not in text.lower()
    assert "my_case_open" in callback_values(markup)
    flow.service_factory.assert_not_called()
    flow.db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", list(payments.M1_PAYMENT_CODES))
async def test_disabled_m1_waits_for_manual_confirmation_without_creating_payment(scenario, code):
    flow = scenario(code, disabled=True)
    original_status = flow.case.status

    await payments.start_payment(flow.callback, flow.db, code)

    text = flow.callback.message.edit_text.await_args.args[0]
    assert "Онлайн-оплата сейчас отключена" in text
    assert "Команда подтвердит фактический платёж" in text
    assert flow.case.status == original_status
    flow.service_factory.assert_not_called()
    flow.db.commit.assert_not_awaited()
    assert callback_values(payments.payment_keyboard(flow.payment)) == ["my_case_open"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,next_callback",
    [
        (ConsultationDescriptionRequired("Сначала опишите ситуацию."), "consult_subject_start"),
        (SlotUnavailableError("Слот уже занят."), "consult_booking_start"),
        (ValueError("Активная консультация не найдена"), "consult_booking_start"),
    ],
)
async def test_m2_description_and_slot_guards_keep_their_recovery_paths(
    scenario, error, next_callback
):
    flow = scenario(PaymentCode.M2_CONSULTATION_PAYMENT)
    flow.service.get_or_create_payment.side_effect = error

    await payments.start_payment(flow.callback, flow.db, PaymentCode.M2_CONSULTATION_PAYMENT)

    markup = flow.callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert next_callback in callback_values(markup)
    flow.service.create_payment_link.assert_not_awaited()
    flow.db.rollback.assert_awaited_once()
    flow.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_m2_without_case_keeps_consultation_prompt(scenario):
    flow = scenario(PaymentCode.M2_CONSULTATION_PAYMENT, active=False)

    await payments.start_payment(flow.callback, flow.db, PaymentCode.M2_CONSULTATION_PAYMENT)

    flow.callback.answer.assert_awaited_once_with(
        "Сначала опишите вопрос и выберите дату и время консультации.", show_alert=True
    )
    flow.service_factory.assert_not_called()


@pytest.mark.asyncio
async def test_telegram_failure_after_commit_does_not_retry_payment_mutation(scenario):
    flow = scenario(
        PaymentCode.M1_INITIAL_PAYMENT,
        edit_error=TelegramBadRequest(method=None, message="Bad Request: message can't be edited"),
    )

    await payments.start_payment(flow.callback, flow.db, PaymentCode.M1_INITIAL_PAYMENT)

    assert flow.events == ["create", "link", "commit", "edit", "answer"]
    flow.db.commit.assert_awaited_once()
    flow.db.rollback.assert_not_awaited()
    flow.service.get_or_create_payment.assert_awaited_once()
    assert "инструкция по доверенности" in flow.callback.message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_disabled_m1_stale_dev_confirmation_does_not_mark_payment_paid(scenario, monkeypatch):
    flow = scenario(PaymentCode.M1_INITIAL_PAYMENT, disabled=True)
    flow.callback.data = f"pay_fake_success:{flow.payment.id}"
    monkeypatch.setattr(payments, "fake_payments_enabled", lambda: True)
    monkeypatch.setattr(
        payments, "get_owned_payment", AsyncMock(return_value=(flow.payment, flow.case))
    )
    webhook_factory = Mock()
    monkeypatch.setattr(payments, "PaymentWebhookService", webhook_factory)

    await payments.fake(flow.callback, flow.db)

    assert "Подтверждение фактического платежа" in flow.callback.message.edit_text.await_args.args[0]
    assert flow.payment.status == PaymentStatus.WAITING_CONFIRMATION
    assert flow.case.status == CaseStatus.M1_WAITING_PAYMENT_30000
    webhook_factory.assert_not_called()
    flow.db.commit.assert_not_awaited()
