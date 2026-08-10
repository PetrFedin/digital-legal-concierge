from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bot import payment_presentation


def view(*, status: str, route: str = "M1") -> SimpleNamespace:
    return SimpleNamespace(case_status=status, route=route)


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
    monkeypatch.setattr(payment_presentation, "payments_disabled", lambda: True)

    result = payment_presentation.offline_m1_payment_presentation(
        view(status=status)
    )

    assert result is not None
    assert label_fragment in result.button_label
    assert next_fragment in result.next_action
    assert result.callback == "payments_open"
    assert "Оплатить" not in result.button_label


def test_online_mode_keeps_normal_guarded_case_action(monkeypatch):
    monkeypatch.setattr(payment_presentation, "payments_disabled", lambda: False)

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


def test_telegram_home_my_case_and_status_share_offline_payment_presentation():
    helper_source = Path("app/bot/payment_presentation.py").read_text(encoding="utf-8")
    my_case_source = Path("app/bot/screens/my_case.py").read_text(encoding="utf-8")
    common_source = Path("app/bot/screens/common.py").read_text(encoding="utf-8")
    client_view_source = Path("app/bot/client_case_view.py").read_text(encoding="utf-8")

    assert "OFFLINE_M1_PAYMENT_PRESENTATIONS" in helper_source
    assert "offline_m1_payment_presentation(view)" in my_case_source
    assert "if offline_m1_payment_presentation(view):" in my_case_source
    assert "def _shown_next_action(view)" in common_source
    assert "offline_m1_payment_presentation(view)" in common_source
    assert "Новый платёж не создавался" in my_case_source

    # Provider availability belongs to presentation/payment execution, not the
    # durable case projection. This keeps persisted history stable across a
    # runtime provider toggle.
    assert "payments_disabled" not in client_view_source
