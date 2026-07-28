from types import SimpleNamespace

import pytest

from app.bot.bot import _callback_analytics
from app.domain.analytics.activity_service import (
    BOT_ACTION,
    BOT_PAYMENT_ACTION,
)


def callback(data: str):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(language_code="ru", is_premium=False),
        message=SimpleNamespace(
            message_id=123,
            chat=SimpleNamespace(type="private"),
        ),
    )


@pytest.mark.parametrize(
    ("value", "expected_action", "expected_event"),
    [
        (
            "consult_reschedule_slot:9223372036854775807",
            "consult_reschedule_slot",
            BOT_ACTION,
        ),
        (
            "consult_select:date:20261231:9223372036854775807",
            "consult_select:date",
            BOT_ACTION,
        ),
        (
            "pay_open:9223372036854775807",
            "pay_open",
            BOT_PAYMENT_ACTION,
        ),
    ],
)
def test_callback_analytics_does_not_store_internal_references(
    value,
    expected_action,
    expected_event,
):
    event_name, payload = _callback_analytics(callback(value))

    assert event_name == expected_event
    assert payload["callback_action"] == expected_action
    assert payload["callback_length"] == len(value)
    assert "callback_data" not in payload
    assert "9223372036854775807" not in str(payload)
    assert "20261231" not in str(payload)


def test_callback_analytics_preserves_screen_classification():
    _, payload = _callback_analytics(
        callback("consult_select:mode:nearest")
    )

    assert payload["screen"] == "consultation"
    assert payload["navigation_type"] == "inline_button"
    assert payload["chat_type"] == "private"
