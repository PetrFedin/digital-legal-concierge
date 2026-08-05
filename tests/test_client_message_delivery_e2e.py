from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from app.bot.screens.messages import (
    HISTORY_PAGE_SIZE,
    HISTORY_TEXT_LIMIT,
    _format_dialog,
    _history_slice,
    message_history,
    message_send,
)
from app.domain.notifications.immediate_delivery import (
    deliver_selected_notifications,
)


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_client_message_event_alerts_lawyer_and_admin_with_case_context():
    rules = read("app/domain/notifications/notification_rules.py")
    templates = read("app/domain/notifications/notification_templates.py")

    assert '"CLIENT_MESSAGE_RECEIVED"' in rules
    assert '"recipients": ["lawyer", "admin"]' in rules
    assert '"template": "client_message_received"' in rules
    assert '"client_message_received"' in templates
    for field in (
        "{case_number}",
        "{category}",
        "{urgency}",
        "{assignment}",
        "{text}",
    ):
        assert field in templates
    assert "Откройте центр сообщений" in templates


def test_inbound_message_and_outbox_commit_before_telegram_delivery():
    source = inspect.getsource(message_send)

    create = source.index("get_or_create_client_message(")
    emit = source.index("notifications = await NotificationEngine(db).emit(")
    durable_commit = source.index("await db.commit()", emit)
    delivery = source.index("await deliver_selected_notifications(", durable_commit)

    assert create < emit < durable_commit < delivery
    assert "source_message_id=message.message_id" in source
    assert 'event_code="CLIENT_MESSAGE_RECEIVED"' in source
    assert 'dedupe_key=f"case:{case.id}:message:{created.id}:client-message"' in source
    assert "if is_new:" in source
    assert "if is_new\n        else" in source
    assert "Bot(" not in source
    assert "send_message" not in source


def test_duplicate_update_does_not_create_a_second_outbox():
    service = read("app/domain/messages/message_service.py")
    bot = read("app/bot/screens/messages.py")

    assert "find_client_message_by_source" in service
    assert "get_or_create_client_message" in service
    assert "return existing, False" in service
    assert "async with self.db.begin_nested()" in service
    assert "except IntegrityError" in service
    assert "source_message_id=source_message_id" in service
    assert "if is_new:" in bot
    assert 'else {"status": "not_required"}' in bot
    assert "повторная запись не создана" in bot


def test_database_enforces_telegram_source_idempotency():
    model = read("app/models/message.py")
    migration = read(
        "migrations/versions/20260805_0012_message_source_idempotency.py"
    )
    migration_test = read("tests/test_database_migrations.py")

    assert '"uq_messages_sender_source_message"' in model
    assert '"sender_type"' in model
    assert '"sender_id"' in model
    assert '"source_message_id"' in model
    assert "BigInteger" in model
    assert 'revision = "20260805_0012"' in migration
    assert 'down_revision = "20260730_0011"' in migration
    assert "inspect(bind)" in migration
    assert "if INDEX_NAME not in indexes" in migration
    assert "HEAD_REVISION = \"20260805_0012\"" in migration_test
    assert "MESSAGE_SOURCE_INDEX" in migration_test


def test_shared_immediate_delivery_never_rolls_back_saved_business_operation():
    source = inspect.getsource(deliver_selected_notifications)

    assert "NotificationSender(db).send_selected(ids)" in source
    assert "asyncio.wait_for" in source
    assert "await db.commit()" in source
    assert source.count("await db.rollback()") >= 2
    assert 'reason="telegram_timeout"' in source
    assert 'reason="delivery_error"' in source
    assert '"status": "queued"' in read(
        "app/domain/notifications/immediate_delivery.py"
    )


def test_history_keeps_legacy_callback_and_adds_safe_pagination():
    source = read("app/bot/screens/messages.py")

    assert "HISTORY_PAGE_SIZE = 5" in source
    assert "HISTORY_ITEM_TEXT_LIMIT = 560" in source
    assert "HISTORY_TEXT_LIMIT = 3800" in source
    assert 'c.data == "message_history"' in source
    assert 'c.data.startswith("message_history:")' in source
    assert 'f"message_history:{page + 1}"' in source
    assert 'f"message_history:{page - 1}"' in source
    assert "message is not modified" in source
    assert "Переписка открыта новым сообщением" in source
    assert "Команда" in source
    assert "Юрист:" not in source


def test_history_page_zero_contains_newest_messages_and_stays_bounded():
    messages = [
        SimpleNamespace(
            id=index,
            sender_type="client" if index % 2 == 0 else "lawyer",
            text="x" * 2000,
            created_at=None,
        )
        for index in range(12)
    ]

    newest, page, total_pages = _history_slice(messages, 0)
    older, older_page, _ = _history_slice(messages, 1)
    oldest, oldest_page, _ = _history_slice(messages, 99)
    text, rendered_page, rendered_total = _format_dialog(messages, 0)

    assert HISTORY_PAGE_SIZE == 5
    assert [item.id for item in newest] == [7, 8, 9, 10, 11]
    assert [item.id for item in older] == [2, 3, 4, 5, 6]
    assert [item.id for item in oldest] == [0, 1]
    assert (page, older_page, oldest_page, total_pages) == (0, 1, 2, 3)
    assert (rendered_page, rendered_total) == (0, 3)
    assert len(text) <= HISTORY_TEXT_LIMIT
    assert "Страница 1 из 3" in text


def test_team_replies_are_marked_read_only_after_visible_delivery():
    source = inspect.getsource(message_history)

    edit = source.index("changed = await _safe_edit(")
    fallback = source.index("await callback.message.answer(text, reply_markup=markup)")
    mark_read = source.index("await service.mark_lawyer_messages_read(")

    assert edit < mark_read
    assert fallback < mark_read
    assert "message_ids=visible_team_ids" in source
    assert 'if item.sender_type == "lawyer"' in source
    assert "await db.rollback()" in source
    assert source.index("text, page, total_pages = _format_dialog") < source.index(
        "await db.rollback()"
    )


def test_unassigned_case_has_honest_admin_fallback_and_no_dead_end():
    source = read("app/bot/screens/messages.py")

    assert "Юрист ещё не назначен — требуется распределение" in source
    assert "Вопрос направлен в административную " in source
    assert "очередь на распределение." in source
    assert "Ответ появится в переписке по делу" in source
    assert "message_history" in source
    assert "message_create" in source
    assert "my_case_open" in source
    assert "nav_home" in source
    assert "Не удалось зарегистрировать вопрос. Текст не сохранён" in source
    assert "Начать отправку заново" in source


def test_critical_urgency_is_visible_but_not_presented_as_a_guarantee():
    source = read("app/bot/screens/messages.py")

    assert "Критично: срок менее 24 часов" in source
    assert "Срочность зафиксирована" in source
    assert "не ждите только ответа в боте" in source
    assert "официальный способ подачи документов" in source
