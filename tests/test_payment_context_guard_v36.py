from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bot.screens.payments import (
    _m1_payment_context_matches,
    payment_keyboard,
    start_payment,
)
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus


@pytest.mark.parametrize(
    ("route", "status", "code", "expected"),
    (
        (RouteCode.M1, CaseStatus.M1_WAITING_PAYMENT_30000, PaymentCode.M1_INITIAL_PAYMENT, True),
        (RouteCode.M1, CaseStatus.M1_POWER_OF_ATTORNEY, PaymentCode.M1_INITIAL_PAYMENT, False),
        (RouteCode.M2, CaseStatus.M2_PAYMENT_PENDING, PaymentCode.M1_INITIAL_PAYMENT, False),
        (RouteCode.M1, CaseStatus.M1_WAITING_PAYMENT_70000, PaymentCode.M1_COURT_PAYMENT, True),
        (RouteCode.M1, CaseStatus.M1_ENFORCEMENT, PaymentCode.M1_COURT_PAYMENT, False),
    ),
)
def test_m1_payment_context_requires_exact_route_and_stage(route, status, code, expected):
    case = SimpleNamespace(route=route, status=status)
    assert _m1_payment_context_matches(case, code) is expected


def test_payment_keyboard_always_has_escape_navigation():
    payment = SimpleNamespace(
        id=321,
        payment_url=None,
        status=PaymentStatus.WAITING_CONFIRMATION,
    )

    markup = payment_keyboard(payment)
    callback_data = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert "payments_open" in callback_data
    assert "my_case_open" in callback_data
    assert "nav_home" in callback_data


def test_start_payment_rejects_stale_m1_context_before_creating_payment():
    source = inspect.getsource(start_payment)

    guard_index = source.index("if is_m1_payment and not _m1_payment_context_matches")
    create_index = source.index("payment = await service.get_or_create_payment")

    assert guard_index < create_index
    assert "Новый платёж не создавался" in source
    assert "str(case.route or \"\") != RouteCode.M2.value" in source


def test_disabled_m1_payment_keeps_pending_obligation_without_provider_link():
    source = inspect.getsource(start_payment)

    disabled_index = source.index("if is_m1_payment and payments_disabled():")
    provider_index = source.index("payment = await service.create_payment_link(payment)")

    assert disabled_index < provider_index
    assert "Платёж уже зафиксирован в системе как ожидающий" in source
    assert "проверки фактического поступления денег" in source


def test_m1_payment_copy_matches_actual_next_stage():
    source = inspect.getsource(start_payment)

    assert "оформление доверенности" in source
    assert "исполнительный этап" in source
    assert "выбранный слот станет окончательно вашим" in source


def test_fake_final_success_opens_closed_archive_navigation():
    source = Path("app/bot/screens/payments.py").read_text(encoding="utf-8")
    final_start = source.index("if payment.payment_code == PaymentCode.M1_SUCCESS_FEE:")
    final_source = source[final_start:]

    assert "Финальный платёж подтверждён. Дело закрыто." in final_source
    assert 'callback_data="payments_open"' in source
    assert '("📁 Итог дела", "my_case_open")' in final_source
    assert '("🕘 История", "case_history_open")' in final_source
    assert '("🏠 Главная", "nav_home")' in final_source
