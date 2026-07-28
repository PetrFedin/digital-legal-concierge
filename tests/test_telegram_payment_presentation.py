from decimal import Decimal

import pytest

from app.bot.screens.payments import _payment_buttons, _payment_card
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.payment import Payment


def payment_for_status(
    status: str,
    *,
    manual_review_required: bool = False,
) -> Payment:
    return Payment(
        id=987654,
        case_id=1,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=status,
        payment_url="https://payments.example.test/current",
        manual_review_required=manual_review_required,
    )


def button_data(markup) -> tuple[list[str], list[str], list[str]]:
    texts = []
    callbacks = []
    urls = []
    for row in markup.inline_keyboard:
        for button in row:
            texts.append(button.text)
            if button.callback_data:
                callbacks.append(button.callback_data)
            if button.url:
                urls.append(button.url)
    return texts, callbacks, urls


def test_client_payment_card_hides_internal_identifier_and_dev_controls():
    payment = payment_for_status(PaymentStatus.WAITING_CONFIRMATION.value)

    full = _payment_card(payment)
    compact = _payment_card(payment, compact=True)
    texts, callbacks, urls = button_data(_payment_buttons(payment))

    assert str(payment.id) not in full
    assert str(payment.id) not in compact
    assert "Платёж №" not in full
    assert "DEV" not in full
    assert all("DEV" not in text for text in texts)
    assert all(not value.startswith("pay_fake_success:") for value in callbacks)
    assert payment.payment_url in urls


@pytest.mark.parametrize(
    "status",
    [
        PaymentStatus.PAID.value,
        PaymentStatus.REFUNDED.value,
        PaymentStatus.CANCELLED.value,
        PaymentStatus.FAILED.value,
        PaymentStatus.EXPIRED.value,
    ],
)
def test_closed_or_invalid_payment_does_not_expose_old_payment_url(status):
    payment = payment_for_status(status)

    _, _, urls = button_data(_payment_buttons(payment))

    assert payment.payment_url not in urls


def test_manual_review_hides_payment_url_and_offers_manager_contact():
    payment = payment_for_status(
        PaymentStatus.WAITING_CONFIRMATION.value,
        manual_review_required=True,
    )

    texts, _, urls = button_data(_payment_buttons(payment))

    assert payment.payment_url not in urls
    assert "💬 Уточнить у менеджера" in texts
    assert "Повторно платить не нужно" in _payment_card(payment)
