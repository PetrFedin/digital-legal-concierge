from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_selected_notification_delivery_reuses_retry_sender():
    source = read("app/domain/notifications/notification_sender.py")

    assert "async def send_selected" in source
    assert "Notification.id.in_(ids)" in source
    assert ".with_for_update(skip_locked=True)" in source
    assert "TelegramRetryAfter" in source
    assert "TelegramNetworkError" in source
    assert 'notification.status = "RETRY"' in source
    assert 'notification.status = "FAILED"' in source
    assert 'notification.status = "SENT"' in source
    assert "async def send_pending" in source
    assert "summary = await self._deliver(notifications)" in source


def test_document_decision_commits_before_immediate_delivery():
    source = read("app/api/document_review.py")
    endpoint = source[source.index("async def review_document"):]

    assert "result.notification_ids" in endpoint
    assert "_deliver_review_notifications" in endpoint
    assert endpoint.index("await db.commit()") < endpoint.index(
        "delivery = await _deliver_review_notifications"
    )
    assert "asyncio.wait_for" in source
    assert "timeout=8" in source
    assert '"status": "queued"' in source
    assert "await db.rollback()" in source
    assert '"delivery": delivery' in source


def test_document_review_returns_only_new_notification_ids():
    source = read("app/domain/documents/document_review_service.py")

    assert "notification_ids: tuple[int, ...] = ()" in source
    assert "ReviewResult(document, case, False, normalized, ())" in source
    assert "notifications = await NotificationEngine(self.db).emit" in source
    assert "notification_ids = tuple" in source
    assert "notification.id is not None" in source


def test_new_upload_supersedes_only_unresolved_previous_versions():
    source = read("app/domain/documents/document_service.py")

    assert "SUPERSEDED_BY_NEW_UPLOAD" in source
    assert "DocumentStatus.ON_REVIEW" in source
    assert "DocumentStatus.NEEDS_REUPLOAD" in source
    assert "DocumentStatus.REJECTED" in source
    assert "DocumentStatus.UPLOADED" not in source.split(
        "SUPERSEDED_BY_NEW_UPLOAD =", 1
    )[1].split("}", 1)[0]
    assert "select(Case).where(Case.id == case.id).with_for_update()" in source
    assert "previous.status = DocumentStatus.ARCHIVED" in source
    assert "DOCUMENT_PENDING_VERSION_SUPERSEDED" in source


def test_approving_new_version_archives_all_previous_active_versions():
    source = read("app/domain/documents/document_review_service.py")

    assert "async def _archive_previous_versions" in source
    assert "Document.version < document.version" in source
    assert "Document.status != DocumentStatus.ARCHIVED" in source
    assert "item.status = DocumentStatus.ARCHIVED" in source
    assert "DOCUMENT_PREVIOUS_VERSIONS_ARCHIVED" in source
    assert 'elif normalized == "approve"' in source


def test_document_review_ui_reports_delivery_without_reversing_saved_decision():
    source = read("app/api/document_review.py")

    assert "function deliveryText" in source
    assert "Клиент уведомлён в Telegram" in source
    assert "Уведомление поставлено в очередь повторной доставки" in source
    assert "Решение сохранено, но Telegram-доставка недоступна" in source
    assert "Решение сохранено, но очередь не обновилась" in source
