from pathlib import Path
from types import SimpleNamespace

from app.domain.notifications.notification_actions import build_notification_reply_markup


ROOT = Path(__file__).resolve().parents[1]


def callbacks(markup) -> list[str]:
    return [row[0].callback_data for row in markup.inline_keyboard]


def notification(event_code: str, recipient_type: str = "client"):
    return SimpleNamespace(
        recipient_type=recipient_type,
        title=recipient_type,
        event_code=event_code,
        dedupe_key=f"case:17:{event_code.lower()}",
    )


def test_claim_preparation_notification_returns_to_current_case_and_message():
    markup = build_notification_reply_markup(
        notification("M1_CLAIM_PREPARATION_STARTED")
    )
    assert markup is not None
    assert callbacks(markup) == ["my_case_open", "message_create"]


def test_claim_sent_notification_opens_real_wait_status_and_history():
    markup = build_notification_reply_markup(notification("M1_CLAIM_SENT"))
    assert markup is not None
    assert callbacks(markup) == ["court_status", "case_history_open", "my_case_open"]


def test_court_started_notification_opens_real_court_status():
    markup = build_notification_reply_markup(notification("COURT_STAGE_STARTED"))
    assert markup is not None
    assert callbacks(markup) == ["court_status", "my_case_open", "message_create"]


def test_claim_navigation_is_client_only_and_within_telegram_callback_limit():
    for event_code in {
        "M1_CLAIM_PREPARATION_STARTED",
        "M1_CLAIM_SENT",
        "COURT_STAGE_STARTED",
    }:
        assert build_notification_reply_markup(
            notification(event_code, "lawyer")
        ) is None
        markup = build_notification_reply_markup(notification(event_code))
        assert markup is not None
        assert all(len(value.encode("utf-8")) <= 64 for value in callbacks(markup))


def test_notification_callbacks_have_real_bot_handlers():
    m1 = (ROOT / "app/bot/screens/m1_stages.py").read_text(encoding="utf-8")
    history = (ROOT / "app/bot/screens/history.py").read_text(encoding="utf-8")
    my_case = (ROOT / "app/bot/screens/my_case.py").read_text(encoding="utf-8")
    messages = (ROOT / "app/bot/screens/messages.py").read_text(encoding="utf-8")

    assert 'c.data == "court_status"' in m1
    assert 'c.data == "case_history_open"' in history
    assert 'c.data == "my_case_open"' in my_case
    assert 'c.data == "message_create"' in messages
