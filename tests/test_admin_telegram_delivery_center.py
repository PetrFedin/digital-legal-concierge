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
    recipient_type: str | None = "client",
    target_chat_id: int | None = 123456789,
    user_id: int | None = 5,
    case_id: int | None = 17,
    last_error: str | None = None,
    next_attempt_at=None,
):
    now = datetime(2026, 8, 5, 10, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=7,
        case_id=case_id,
        user_id=user_id,
        event_code="DOCUMENT_REJECTED",
        title=recipient_type,
        text="Документ нужно заменить",
        status=status,
        recipient_type=recipient_type,
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
    assert item["target_recoverable"] is True
    assert item["recommended_action"] == (
        "Повторно определить Telegram-адрес из дела или профиля"
    )
    assert item["retry_label"] == "Повторно определить адрес"
    assert item["can_retry"] is True
    assert "target_chat_id" not in item


def test_sender_recipient_matrix_does_not_offer_false_retry():
    admin = serialize_notification(
        notification(
            "FAILED",
            recipient_type="admin",
            target_chat_id=None,
            user_id=None,
            case_id=None,
        )
    )
    lawyer_without_case = serialize_notification(
        notification(
            "FAILED",
            recipient_type="lawyer",
            target_chat_id=None,
            user_id=8,
            case_id=None,
        )
    )
    unknown_with_case = serialize_notification(
        notification(
            "FAILED",
            recipient_type="external",
            target_chat_id=None,
            user_id=None,
            case_id=17,
        )
    )

    assert admin["target_recoverable"] is True
    assert admin["can_retry"] is True
    assert lawyer_without_case["target_recoverable"] is False
    assert lawyer_without_case["can_retry"] is False
    assert unknown_with_case["target_recoverable"] is False
    assert unknown_with_case["can_retry"] is False
    assert unknown_with_case["recommended_action"] == (
        "Исправить источник уведомления: получатель не связан с системой"
    )


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
    assert "Telegram sender может определить повторно" in source
    assert "TELEGRAM_NOTIFICATION_RETRY_REQUESTED" in source
    assert 'notification.status = "PENDING"' in source
    assert 'notification.status = "SENT"' not in source
    assert source.count('Notification.status.in_(["PENDING", "RETRY"])') >= 2
    assert "def _recoverable_target_clause" in source
    assert source.count(".where(_recoverable_target_clause())") == 2
    assert 'recipient == "client"' in source
    assert 'recipient == "lawyer"' in source
    assert 'recipient == "admin"' in source
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
    assert "TELEGRAM_NOTIFICATION_DUE_BATCH_REQUESTED" in source
    assert "notification_ids\": list(ids)" in source
    assert "mark-sent" not in source
    assert "@router.delete" not in source
    assert "method:'DELETE'" not in source
    assert 'method:"DELETE"' not in source


def test_delivery_ui_is_protected_and_has_complete_recovery():
    source = read("app/api/notification_delivery.py")

    assert "request.cookies.get(settings.admin_session_cookie)" in source
    assert "require_admin(_admin_token(request, x_admin_token))" in source
    assert 'RedirectResponse(url="/login", status_code=303)' in source
    assert "Требуют внимания" in source
    assert "Не доставлено" in source
    assert "На повторе" in source
    assert "доставлено за 24 часа" in source
    assert "Отправить доступные сейчас" in source
    assert "retry_label" in source
    assert "${esc(x.retry_label||'Повторить сейчас')}" in source
    assert "адрес будет найден из дела или профиля" in source
    assert "const pending=new Set()" in source
    assert "aria-busy" in source
    assert "Не удалось загрузить доставку" in source
    assert "Повторить</button>" in source
    assert "Отправка выполнена, но экран не обновился" in source
    assert "target_recoverable" in source
    assert "получатель не связан с системой" in source
    assert "target_chat_id" not in source[source.index("DELIVERY_HTML"):]
    assert "BOT_TOKEN" not in source[source.index("DELIVERY_HTML"):]


def test_delivery_case_deep_link_is_exact_and_protected():
    api = read("app/api/notification_delivery.py")
    page = read("app/admin/case_detail_page.py")

    assert '@router.get("/case/{case_id}/ui"' in api
    assert "CASE_DETAIL_HTML.replace" in api
    assert 'href="/admin/notification-delivery/case/${x.case_id}/ui"' in api
    assert 'href="/admin-ui">Открыть дело' not in api
    assert "require_admin(_admin_token(request, x_admin_token))" in api

    assert "'/admin/case-workspace/'+caseId" in page
    assert "Актуальных документов нет" in page
    assert "История версий" in page
    assert "Рекомендуемое действие" in page
    assert "Не удалось открыть дело" in page
    assert 'href="/admin/notification-delivery/ui"' in page
    assert "target_chat_id" not in page
    assert "BOT_TOKEN" not in page


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
