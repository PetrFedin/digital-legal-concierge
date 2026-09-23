from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bot import payment_presentation
from app.bot.screens import payments as payments_screen
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus


def view(*, status: str, route: str = "M1") -> SimpleNamespace:
    return SimpleNamespace(case_status=status, route=route)


def payment(
    *,
    code: str = PaymentCode.M1_INITIAL_PAYMENT,
    status: PaymentStatus = PaymentStatus.PENDING,
    payment_url: str | None = None,
    provider: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        payment_code=code,
        status=status,
        payment_url=payment_url,
        provider=provider,
    )


@pytest.mark.parametrize(
    ("status", "label_fragment", "next_fragment"),
    [
        (
            "M1_WAITING_PAYMENT_30000",
            "Проверить первый платёж",
            "Первый платёж ожидает подтверждения командой",
        ),
        (
            "M1_WAITING_PAYMENT_70000",
            "Проверить второй платёж",
            "Второй платёж ожидает подтверждения командой",
        ),
        (
            "M1_WAITING_SUCCESS_FEE",
            "Проверить финальный платёж",
            "Финальный платёж ожидает подтверждения командой",
        ),
    ],
)
def test_offline_m1_waiting_payment_points_to_existing_payment_status(
    monkeypatch,
    status,
    label_fragment,
    next_fragment,
):
    monkeypatch.setattr(payment_presentation, "payments_offline", lambda: True)

    result = payment_presentation.offline_m1_payment_presentation(
        view(status=status)
    )

    assert result is not None
    assert label_fragment in result.button_label
    assert next_fragment in result.next_action
    assert result.callback == "payments_open"
    assert "Оплатить" not in result.button_label


def test_online_mode_keeps_normal_guarded_case_action(monkeypatch):
    monkeypatch.setattr(payment_presentation, "payments_offline", lambda: False)

    assert (
        payment_presentation.offline_m1_payment_presentation(
            view(status="M1_WAITING_PAYMENT_30000")
        )
        is None
    )


def test_offline_presentation_does_not_override_m2(monkeypatch):
    monkeypatch.setattr(payment_presentation, "payments_disabled", lambda: True)

    assert (
        payment_presentation.offline_m1_payment_presentation(
            view(status="M2_PAYMENT_PENDING", route="M2")
        )
        is None
    )


def test_offline_m1_payment_row_says_team_confirmation_not_pay_again(monkeypatch):
    monkeypatch.setattr(payments_screen, "payments_offline", lambda: True)
    row = payment()

    assert payments_screen.client_payment_status_label(row) == (
        "Ожидает подтверждения командой"
    )
    note = payments_screen.client_payment_status_note(row)
    assert "Новый платёж через бот создавать не нужно" in note
    assert "проверки фактического поступления" in note


def test_provider_toggle_does_not_relabel_existing_online_payment_as_offline(monkeypatch):
    monkeypatch.setattr(payments_screen, "payments_disabled", lambda: True)
    row = payment(
        status=PaymentStatus.WAITING_CONFIRMATION,
        payment_url="https://provider.example/pay/123",
        provider="provider",
    )

    assert payments_screen.client_payment_status_label(row) == "Ожидает подтверждения"
    assert payments_screen.client_payment_status_note(row) == ""


def test_offline_label_covers_m1_and_m2_real_obligations(monkeypatch):
    monkeypatch.setattr(payments_screen, "payments_offline", lambda: True)
    m2 = payment(
        code=PaymentCode.M2_CONSULTATION_PAYMENT,
        provider="offline",
    )
    paid_m1 = payment(status=PaymentStatus.PAID)

    assert payments_screen.client_payment_status_label(m2) == (
        "Ожидает подтверждения командой"
    )
    assert "после перевода" in payments_screen.client_payment_status_note(m2).lower()
    assert payments_screen.client_payment_status_label(paid_m1) == "Оплачено"
    assert payments_screen.client_payment_status_note(paid_m1) == ""


def test_telegram_home_my_case_and_status_share_offline_payment_presentation():
    helper_source = Path("app/bot/payment_presentation.py").read_text(encoding="utf-8")
    my_case_source = Path("app/bot/screens/my_case.py").read_text(encoding="utf-8")
    common_source = Path("app/bot/screens/common.py").read_text(encoding="utf-8")
    payments_source = Path("app/bot/screens/payments.py").read_text(encoding="utf-8")
    client_view_source = Path("app/bot/client_case_view.py").read_text(encoding="utf-8")

    assert "OFFLINE_M1_PAYMENT_PRESENTATIONS" in helper_source
    assert "offline_m1_payment_presentation(view)" in my_case_source
    assert "if offline_m1_payment_presentation(view):" in my_case_source
    assert "def _shown_next_action(view)" in common_source
    assert "offline_m1_payment_presentation(view)" in common_source
    assert "Новый платёж не создавался" in my_case_source
    assert "client_payment_status_label(payment)" in payments_source
    assert "client_payment_status_note(payment)" in payments_source
    assert "not payment.payment_url" in payments_source
    assert "not payment.provider" in payments_source

    # Provider availability belongs to presentation/payment execution, not the
    # durable case projection. This keeps persisted history stable across a
    # runtime provider toggle.
    assert "payments_offline" not in client_view_source
    assert "payments_disabled" not in client_view_source
