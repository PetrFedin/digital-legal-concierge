from __future__ import annotations

import inspect
from pathlib import Path

from app.api.message_center import MESSAGE_CENTER_HTML, message_center_status
from app.domain.messages.message_priority import (
    CLIENT_URGENCY_NOTE,
    PRIORITY_CRITICAL,
    PRIORITY_NORMAL,
    PRIORITY_TODAY,
    present_message,
    queue_bucket,
)


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_structured_client_message_is_presented_without_technical_headers():
    view = present_message(
        "Тема: Документы\n"
        "Срочность: Критично: срок менее 24 часов\n\n"
        "Нужно сегодня подать возражения.",
        sender_type="client",
    )

    assert view.body == "Нужно сегодня подать возражения."
    assert view.category == "Документы"
    assert view.urgency == "Критично: срок менее 24 часов"
    assert view.priority == PRIORITY_CRITICAL
    assert view.priority_label == "Клиент указал срок менее 24 часов"
    assert view.priority_rank == 0
    assert view.structured is True


def test_today_normal_unknown_and_team_messages_have_safe_fallbacks():
    today = present_message(
        "Тема: Ход дела\nСрочность: Нужен ответ сегодня\n\nЧто изменилось?",
        sender_type="client",
    )
    normal = present_message(
        "Тема: Другое\nСрочность: Обычный\n\nОбычный вопрос.",
        sender_type="client",
    )
    legacy = present_message("Старое сообщение без заголовков", sender_type="client")
    team = present_message(
        "Тема: не должна распознаваться\nСрочность: Обычный\n\nОтвет",
        sender_type="lawyer",
    )

    assert today.priority == PRIORITY_TODAY
    assert normal.priority == PRIORITY_NORMAL
    assert legacy.body == "Старое сообщение без заголовков"
    assert legacy.category is None
    assert legacy.structured is False
    assert team.category is None
    assert team.urgency is None
    assert team.structured is False


def test_objective_overdue_outranks_self_reported_urgency():
    assert queue_bucket(
        waiting_for_reply=True,
        overdue=True,
        priority=PRIORITY_NORMAL,
    ) == 0
    assert queue_bucket(
        waiting_for_reply=True,
        overdue=False,
        priority=PRIORITY_CRITICAL,
    ) == 1
    assert queue_bucket(
        waiting_for_reply=True,
        overdue=False,
        priority=PRIORITY_TODAY,
    ) == 2
    assert queue_bucket(
        waiting_for_reply=True,
        overdue=False,
        priority=PRIORITY_NORMAL,
    ) == 3
    assert queue_bucket(
        waiting_for_reply=False,
        overdue=True,
        priority=PRIORITY_CRITICAL,
    ) == 4


def test_status_api_exposes_priority_counts_clean_preview_and_queue_bucket():
    source = inspect.getsource(message_center_status)

    assert "latest_view = present_message(" in source
    assert '"latest_preview": latest_view.body[:1000]' in source
    assert '"critical_count": critical_count' in source
    assert '"today_count": today_count' in source
    assert '"unassigned_count": unassigned_count' in source
    assert '"priority_note": CLIENT_URGENCY_NOTE' in source
    assert "bucket = queue_bucket(" in source
    assert "for bucket in range(5)" in source
    assert 'bool(item["unassigned"])' in source
    assert "latest_view.priority == PRIORITY_CRITICAL" in source
    assert "latest_view.priority == PRIORITY_TODAY" in source


def test_staff_ui_has_actionable_filters_and_never_calls_client_urgency_sla():
    for value in (
        'value="attention"',
        'value="critical"',
        'value="today"',
        'value="unassigned"',
        'value="overdue"',
    ):
        assert value in MESSAGE_CENTER_HTML
    for metric in (
        "critical_count",
        "today_count",
        "unassigned_count",
        "overdue_count",
    ):
        assert metric in MESSAGE_CENTER_HTML
    assert "Срочность клиента — сигнал для сортировки" in MESSAGE_CENTER_HTML
    assert "не подтверждённый SLA" in MESSAGE_CENTER_HTML
    assert "Фактическая просрочка 4+ часа" in MESSAGE_CENTER_HTML
    assert "Юрист не назначен" in MESSAGE_CENTER_HTML
    assert "latest_preview" in MESSAGE_CENTER_HTML
    assert "m.body??m.text" in MESSAGE_CENTER_HTML
    assert "priority_note" in MESSAGE_CENTER_HTML
    assert CLIENT_URGENCY_NOTE not in MESSAGE_CENTER_HTML


def test_web_and_bot_share_one_immediate_delivery_service():
    api_source = read("app/api/message_center.py")
    bot_source = read("app/bot/screens/messages.py")
    delivery_source = read("app/domain/notifications/immediate_delivery.py")

    assert "deliver_selected_notifications" in api_source
    assert "deliver_selected_notifications" in bot_source
    assert "NotificationSender(db).send_selected(ids)" in delivery_source
    assert "asyncio.wait_for" in delivery_source
    assert "NotificationSender" not in api_source
    assert "asyncio.wait_for" not in api_source
