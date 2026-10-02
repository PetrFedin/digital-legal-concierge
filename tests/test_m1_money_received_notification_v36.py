from pathlib import Path
from types import SimpleNamespace

from app.domain.notifications.notification_actions import build_notification_reply_markup
from app.domain.notifications.notification_templates import TEMPLATES


ROOT = Path(__file__).resolve().parents[1]


def notification(
    recipient_type: str = "client",
    *,
    event_code: str = "M1_MONEY_RECEIVED",
):
    return SimpleNamespace(
        recipient_type=recipient_type,
        title=recipient_type,
        event_code=event_code,
        dedupe_key=f"case:17:{event_code.lower()}:v36",
    )


def callbacks(markup) -> list[str]:
    return [row[0].callback_data for row in markup.inline_keyboard]


def test_money_received_notification_has_one_real_payment_entry_and_safe_fallbacks():
    markup = build_notification_reply_markup(notification())

    assert markup is not None
    assert callbacks(markup) == ["pay_success_fee", "my_case_open", "message_create"]
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks(markup))


def test_money_received_action_is_client_only():
    assert build_notification_reply_markup(notification("lawyer")) is None


def test_money_received_template_explains_amount_fee_and_terminal_effect():
    text = TEMPLATES["m1_money_received"].format(
        case_number="M1-17",
        amount="250000.00",
        success_fee="25000.00",
    )

    assert "250000.00" in text
    assert "25000.00" in text
    assert "закрыто" in text.lower()


def test_money_received_callback_is_wired_to_real_bot_handler():
    stages = (ROOT / "app/bot/screens/m1_stages.py").read_text(encoding="utf-8")

    assert 'callback_matches_action(c.data, "pay_success_fee")' in stages
    assert "PaymentCode.M1_SUCCESS_FEE" in stages


def test_closed_notification_routes_only_to_read_only_terminal_views():
    markup = build_notification_reply_markup(
        notification(event_code="M1_CLOSED")
    )

    assert markup is not None
    assert callbacks(markup) == [
        "my_case_open",
        "documents_open",
        "case_history_open",
        "payments_open",
    ]
    assert "message_create" not in callbacks(markup)
    assert "pay_success_fee" not in callbacks(markup)
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks(markup))


def test_closed_template_is_explicitly_terminal_and_read_only():
    text = TEMPLATES["m1_closed"].format(case_number="M1-17")

    assert "подтверждён" in text.lower()
    assert "дело закрыто" in text.lower()
    assert "только для просмотра" in text.lower()
