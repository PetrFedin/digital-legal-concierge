from __future__ import annotations

import inspect
from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.screens.payments import (
    _present_committed_callback,
    fake,
    money,
    open_payment,
    payment_action_label,
    payment_status_label,
    payment_summary_line,
    payments,
    start_payment,
)
from app.domain.statuses.payment_statuses import PaymentStatus


class FakeMessage:
    def __init__(self, edit_error: Exception | None = None):
        self.edit_error = edit_error
        self.edits = []
        self.answers = []

    async def edit_text(self, text, reply_markup=None):
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append((text, reply_markup))

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, edit_error: Exception | None = None):
        self.message = FakeMessage(edit_error=edit_error)


def _payment(*, status=PaymentStatus.PENDING):
    return SimpleNamespace(
        id=987654,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        status=status,
    )


def test_payment_copy_hides_internal_ids_and_machine_statuses():
    payment = _payment()

    summary = payment_summary_line(payment)
    action = payment_action_label(payment)

    assert summary == "• Оплата консультации\n  5 000,00 ₽ · Ожидает оплаты"
    assert action == "Открыть: Оплата консультации"
    assert str(payment.id) not in summary + action
    assert PaymentStatus.PENDING.value not in summary
    assert payment_status_label(PaymentStatus.PAID_REVIEW) == "Получено, проверяется командой"
    assert payment_status_label("REMOVED_STATUS") == "Статус уточняется"
    assert money(Decimal("30000")) == "30 000,00 ₽"


def test_payment_list_and_card_do_not_render_raw_payment_ids_or_statuses():
    list_source = inspect.getsource(payments)
    open_source = inspect.getsource(open_payment)

    assert '#{payment.id}' not in list_source
    assert '{payment.status}' not in list_source
    assert '{payment.status}' not in open_source
    assert "payment_summary_line(payment)" in list_source
    assert "payment_action_label(payment)" in list_source
    assert "payment_status_label(payment.status)" in open_source


@pytest.mark.asyncio
async def test_committed_payment_result_falls_back_to_new_message_when_edit_is_stale():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message can't be edited",
        )
    )

    await _present_committed_callback(
        callback,
        "✅ Платёж уже сохранён.",
        reply_markup=None,
    )

    assert callback.message.answers == [("✅ Платёж уже сохранён.", None)]


@pytest.mark.asyncio
async def test_not_modified_committed_result_does_not_duplicate_message():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message is not modified",
        )
    )

    await _present_committed_callback(
        callback,
        "✅ Уже актуально.",
        reply_markup=None,
    )

    assert callback.message.answers == []


def test_payment_link_is_committed_before_telegram_presentation():
    source = inspect.getsource(start_payment)
    commit = source.index("await db.commit()")
    present = source.index("await _present_committed_callback(")

    assert commit < present
    assert "Платёжный сервис временно недоступен" in source
    assert "Выбранное время, вопрос и документы сохранены" in source


def test_fake_payment_commit_is_separate_from_telegram_presentation():
    source = inspect.getsource(fake)
    commit = source.index("await db.commit()")
    present = source.index("await _present_committed_callback(")

    assert commit < present
    assert "await db.rollback()" in source[:present]
    assert "Оплата пока не подтверждена. Данные дела сохранены." in source


def test_stale_payment_open_callback_has_recovery_path():
    source = inspect.getsource(open_payment)

    assert "except (TypeError, ValueError)" in source
    assert "Эта кнопка оплаты больше не актуальна" in source
    assert "payments_open" in source
    assert "my_case_open" in source
    assert "nav_home" in source
