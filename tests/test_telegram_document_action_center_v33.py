from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bot.screens.document_action_center import _next_action
from app.domain.notifications.notification_actions import (
    build_notification_reply_markup,
)
from app.domain.notifications.notification_sender import NotificationSender
from app.models.notification import Notification


ROOT = Path(__file__).resolve().parents[1]


def _callbacks(markup) -> list[str]:
    return [row[0].callback_data for row in markup.inline_keyboard]


def test_reupload_notification_points_to_exact_reviewed_snapshot():
    notification = SimpleNamespace(
        recipient_type="client",
        title="client",
        event_code="DOCUMENT_REUPLOAD_REQUESTED",
        dedupe_key="case:17:document:42:review:3:request_reupload:client:1001",
    )

    markup = build_notification_reply_markup(notification)

    assert markup is not None
    assert _callbacks(markup)[0] == "document_reupload:42:3"
    assert "documents_open" in _callbacks(markup)
    assert "my_case_open" in _callbacks(markup)


def test_rejected_document_notification_has_same_safe_direct_replacement():
    notification = SimpleNamespace(
        recipient_type="client",
        title="client",
        event_code="DOCUMENT_REJECTED",
        dedupe_key="case:9:document:88:review:2:reject:client:222",
    )

    markup = build_notification_reply_markup(notification)

    assert markup is not None
    assert _callbacks(markup)[0] == "document_reupload:88:2"


def test_document_notification_without_snapshot_degrades_to_safe_navigation():
    notification = SimpleNamespace(
        recipient_type="client",
        title="client",
        event_code="DOCUMENT_REUPLOAD_REQUESTED",
        dedupe_key="legacy-document-notification",
    )

    markup = build_notification_reply_markup(notification)

    assert markup is not None
    assert _callbacks(markup) == ["documents_open", "my_case_open"]


def test_staff_reply_notification_opens_dialog_and_reply_action():
    notification = SimpleNamespace(
        recipient_type="client",
        title="client",
        event_code="STAFF_MESSAGE_REPLIED",
        dedupe_key="case:1:staff-reply:10:client:20",
    )

    markup = build_notification_reply_markup(notification)

    assert markup is not None
    assert _callbacks(markup) == [
        "message_history",
        "message_create",
        "my_case_open",
    ]


def test_non_client_notification_never_gets_client_navigation():
    notification = SimpleNamespace(
        recipient_type="lawyer",
        title="lawyer",
        event_code="DOCUMENT_REUPLOAD_REQUESTED",
        dedupe_key="case:17:document:42:review:3:request_reupload:lawyer:1002",
    )

    assert build_notification_reply_markup(notification) is None


def test_document_action_center_prioritizes_one_specific_replacement():
    case = SimpleNamespace(route="M1", status="M1_DOCUMENTS_RECEIVED")
    replacement = SimpleNamespace(
        id=42,
        title="ДДУ",
        version=3,
        status="NEEDS_REUPLOAD",
        lawyer_comment="Добавьте все страницы и подпись",
    )
    another = SimpleNamespace(
        id=43,
        title="Приложение",
        version=1,
        status="APPROVED",
        lawyer_comment=None,
    )

    text, buttons = _next_action(case, [replacement, another])

    assert "ДДУ" in text
    assert "Добавьте все страницы" in text
    assert buttons == [("🔁 Заменить «ДДУ»", "document_reupload:42:3")]


def test_all_approved_documents_return_to_case_next_action():
    case = SimpleNamespace(route="M1", status="M1_DOCUMENTS_RECEIVED")
    approved = SimpleNamespace(
        id=5,
        title="ДДУ",
        version=2,
        status="APPROVED",
        lawyer_comment=None,
    )

    text, buttons = _next_action(case, [approved])

    assert "Все актуальные документы приняты" in text
    assert buttons == [("📁 К следующему шагу дела", "my_case_open")]


class _FakeDb:
    def __init__(self):
        self.flushed = False

    async def flush(self):
        self.flushed = True


class _ActionBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_sender_delivers_actionable_markup_without_changing_sent_contract():
    db = _FakeDb()
    bot = _ActionBot()
    notification = Notification(
        case_id=11,
        user_id=22,
        channel="telegram",
        event_code="DOCUMENT_REUPLOAD_REQUESTED",
        recipient_type="client",
        target_chat_id=123456,
        title="client",
        text="Нужна новая версия ДДУ",
        status="PENDING",
        is_sent=False,
        dedupe_key="case:11:document:77:review:4:request_reupload:client:123456",
    )

    summary = await NotificationSender(db, bot=bot)._deliver([notification])

    assert summary == {"processed": 1, "sent": 1, "retry": 0, "failed": 0}
    assert db.flushed is True
    assert notification.status == "SENT"
    assert notification.is_sent is True
    assert len(bot.calls) == 1
    assert bot.calls[0]["chat_id"] == 123456
    assert _callbacks(bot.calls[0]["reply_markup"])[0] == "document_reupload:77:4"


def test_direct_reupload_handler_is_snapshot_safe_and_routed_before_legacy_docs():
    action_source = (ROOT / "app/bot/screens/document_action_center.py").read_text(
        encoding="utf-8"
    )
    bot_source = (ROOT / "app/bot/bot.py").read_text(encoding="utf-8")

    assert 'c.data.startswith("document_reupload:")' in action_source
    assert "expected_version = int(parts[2])" in action_source
    assert "int(document.case_id) != int(case.id)" in action_source
    assert "_status(document) not in _REPLACEMENT_STATUSES" in action_source
    assert "Document.version.desc()" in action_source
    assert "int(latest.id) != int(document.id)" in action_source
    assert "replacement_expected_version=expected_version" in action_source
    assert "DocumentUploadStates.waiting_file" in action_source
    assert bot_source.index("document_action_center.router") < bot_source.index(
        "documents.router"
    )
