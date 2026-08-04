from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.admin.notification_delivery import serialize_notification


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def notification(
    status: str,
    *,
    target_chat_id: int | None = 123456789,
    last_error: str | None = None,
    next_attempt_at=None,
):
    now = datetime(2026, 8, 5, 10, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=7,
        case_id=17,
        event_code="DOCUMENT_REJECTED",
        title="client",
        text="Документ нужно заменить",
        status=status,
        recipient_type="client",
        target_chat_id=target_chat_id,
        attempt_count=2,
        last_error=last_error,
        next_attempt_at=next_attempt_at,
        sent_at=now if status == "SENT" else None,
        created_at=now - timedelta(hours=1),
        updated_at=now,
    )


def test_delivery_serializer_is_human_and_does_not_expose_chat_id():
    item = serialize_notification(
        notification(
            "FAILED",
            target_chat_id=None,
            last_error="Не найден Telegram chat ID для получателя client",
        ),
        now=datetime(2026, 8, 5, 11, tzinfo=timezone.utc),
    )

    assert item["status_label"] == "Не доставлено"
    assert item["recipient"] == "Клиент"
    assert item["target_available"] is False
    assert item["recommended_action"] == "Уточнить Telegram получателя"
    assert item["can_retry"] is True
    assert "target_chat_id" not in item


def test_retry_due_and_sent_states_have_distinct_actions():
    now = datetime(2026, 8, 5, 11, tzinfo=timezone.utc)
    retry = serialize_notification(
        notification("RETRY", next_attempt_at=now - timedelta(minutes=1)),
        now=now,
    )
    sent = serialize_notification(notification("SENT"), now=now)

    assert retry["due_now"] is True
    assert retry["recommended_action"] == "Можно отправить сейчас"
    assert sent["can_retry"] is False
    assert sent["recommended_action"] == "Доставка завершена"


def test_delivery_service_has_filters_summary_lock_snapshot_and_audit():
    source = read("app/admin/notification_delivery.py")

    for filter_name in (
        '"attention"',
        '"failed"',
        '"retry"',
        '"pending"',
        '"sent"',
        '"all"',
    ):
        assert filter_name in source
    assert '"due_now"' in source
    assert '"sent_recent"' in source
    assert ".with_for_update()" in source
    assert "expected_status" in source
    assert "expected_updated_at" in source
    assert 'str(notification.status) == "SENT"' in source
    assert "уже доставлено и не может быть отправлено повторно" in source
    assert "TELEGRAM_NOTIFICATION_RETRY_REQUESTED" in source
    assert 'notification.status = "PENDING"' in source
    assert 'notification.status = "SENT"' not in source
    assert 'Notification.status.in_(["PENDING", "RETRY"])' in source
    assert "safe_limit = min(max(int(limit or 50), 1), 50)" in source


def test_retry_api_commits_request_before_selective_delivery():
    source = read("app/api/notification_delivery.py")

    prepare = source.index("notification = await service.prepare_retry(")
    durable_commit = source.index("await db.commit()", prepare)
    delivery = source.index("_attempt_delivery(db, (int(notification.id),))", prepare)

    assert prepare < durable_commit < delivery
    assert "NotificationSender(db).send_selected(notification_ids)" in source
    assert "asyncio.wait_for" in source
    assert "telegram_timeout" in source
    assert "await db.rollback()" in source
    assert '@router.post("/retry-due")' in source
    assert '@router.post("/{notification_id}/retry")' in source
    assert "mark-sent" not in source
    assert "delete" not in source.lower()


def test_delivery_ui_has_filters_single_flight_recovery_and_no_secrets():
    source = read("app/api/notification_delivery.py")

    assert "Требуют внимания" in source
    assert "Не доставлено" in source
    assert "На повторе" in source
    assert "Доставлено за 24 часа" in source.lower() or "доставлено за 24 часа" in source
    assert "Отправить доступные сейчас" in source
    assert "Повторить сейчас" in source
    assert "const pending=new Set()" in source
    assert "aria-busy" in source
    assert "Не удалось загрузить доставку" in source
    assert "Повторить</button>" in source
    assert "Отправка выполнена, но экран не обновился" in source
    assert "target_available" in source
    assert "target_chat_id" not in source[source.index("DELIVERY_HTML"):]
    assert "BOT_TOKEN" not in source[source.index("DELIVERY_HTML"):]


def test_operator_and_dashboard_make_delivery_center_discoverable():
    operator = read("app/api/operator.py")
    dashboard = read("app/admin/admin_dashboard.py")

    assert 'href="/admin/notification-delivery/ui"' in operator
    assert '"telegram_delivery": "/admin/notification-delivery/ui"' in operator
    assert "notification_delivery_router" in operator
    assert "router.include_router(notification_delivery_router)" in operator

    assert "from app.models.notification import Notification" in dashboard
    assert '"notifications"' in dashboard
    assert '"telegram_delivery"' in dashboard
    assert '"workspace": "/admin/notification-delivery/ui"' in dashboard
    assert "notification_attention" in dashboard
