from __future__ import annotations

import inspect

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.screens import consent_flow, m1_stages
from app.bot.screens.m1_stages import _present_committed_result


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


@pytest.mark.asyncio
async def test_committed_m1_result_falls_back_to_new_message_when_edit_is_stale():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message can't be edited",
        )
    )

    await _present_committed_result(
        callback,
        "✅ Этап уже сохранён.",
        reply_markup=None,
    )

    assert callback.message.answers == [("✅ Этап уже сохранён.", None)]


@pytest.mark.asyncio
async def test_committed_m1_result_does_not_duplicate_unchanged_message():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message is not modified",
        )
    )

    await _present_committed_result(
        callback,
        "✅ Экран уже актуален.",
        reply_markup=None,
    )

    assert callback.message.answers == []


def test_m1_stage_router_does_not_shadow_canonical_consent_flow():
    m1_source = inspect.getsource(m1_stages)
    consent_source = inspect.getsource(consent_flow)

    for callback_name in ("consent_open", "consent_accept", "consent_decline"):
        assert f'c.data == "{callback_name}"' not in m1_source
        assert f'c.data == "{callback_name}"' in consent_source


def test_mutating_m1_actions_commit_before_recoverable_presentation():
    for handler in (
        m1_stages.contract_sign,
        m1_stages.poa_done,
        m1_stages.pay_success_fee,
    ):
        source = inspect.getsource(handler)
        commit = source.index("await db.commit()")
        presenter = source.index("await _present_committed_result(", commit)
        assert commit < presenter
        assert "await db.rollback()" not in source[presenter:]


def test_contract_and_poa_stale_paths_return_to_current_case():
    contract_source = inspect.getsource(m1_stages.contract_sign)
    poa_source = inspect.getsource(m1_stages.poa_done)

    assert "if not case:" in contract_source
    assert "if not case:" in poa_source
    assert "_show_stale_stage" in contract_source
    assert "_show_stale_stage" in poa_source
    assert "Дело не изменено" in contract_source
    assert "Повторная запись не создавалась" in poa_source


def test_court_payment_cannot_start_before_explicit_payment_stage():
    court_source = inspect.getsource(m1_stages.court_status)
    payment_source = inspect.getsource(m1_stages.pay_court)

    assert "status == CaseStatus.M1_COURT_STAGE" in court_source
    court_stage_block = court_source.split(
        "status == CaseStatus.M1_COURT_STAGE", 1
    )[1].split("status == CaseStatus.M1_WAITING_PAYMENT_70000", 1)[0]
    assert "pay_court_70000" not in court_stage_block
    assert "_status(case) != CaseStatus.M1_WAITING_PAYMENT_70000" in payment_source
    assert "Новый платёж не создавался" in payment_source


def test_disabled_payment_mode_never_offers_online_m1_payment_cta():
    contract_source = inspect.getsource(m1_stages.contract_sign)
    court_source = inspect.getsource(m1_stages.court_status)
    success_source = inspect.getsource(m1_stages.pay_success_fee)

    assert "if payments_disabled():" in contract_source
    assert "if payments_disabled():" in court_source
    assert "if payments_disabled():" in success_source
    assert "Онлайн-оплата сейчас отключена" in contract_source
    assert "Онлайн-оплата сейчас отключена" in court_source
    assert "Онлайн-оплата сейчас отключена" in success_source


def test_success_fee_is_derived_from_actual_receipt_before_status_change():
    source = inspect.getsource(m1_stages.pay_success_fee)

    estimate = source.index("estimate_success_fee_for_case")
    transition = source.index("next_status=CaseStatus.M1_WAITING_SUCCESS_FEE")
    assert estimate < transition
    assert "existing.amount != amount" in source
    assert "фактическому поступлению" in source
    assert "case.success_fee_amount = amount" in source


def test_success_fee_uses_shared_readable_payment_ui_after_commit():
    source = inspect.getsource(m1_stages.pay_success_fee)
    commit = source.index("await db.commit()")
    after_commit = source[commit:]

    assert "from app.bot.screens.payments import money, payment_keyboard" in after_commit
    assert "money(payment.amount)" in after_commit
    assert "payment_keyboard(payment)" in after_commit
    assert "_money(" not in source
    assert "Сумма: {payment.amount}" not in source
