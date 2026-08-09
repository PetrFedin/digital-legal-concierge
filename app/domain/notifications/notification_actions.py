from __future__ import annotations

import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.models.notification import Notification

_DOCUMENT_REVIEW_DEDUPE = re.compile(
    r"(?:^|:)document:(?P<document_id>\d+):review:"
    r"(?P<version>\d+):(?P<decision>request_reupload|reject)(?:$|:)"
)


def _markup(*rows: tuple[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=callback_data)]
            for label, callback_data in rows
        ]
    )


def _document_reupload_callback(notification: Notification) -> str | None:
    match = _DOCUMENT_REVIEW_DEDUPE.search(str(notification.dedupe_key or ""))
    if not match:
        return None
    return (
        "document_reupload:"
        f"{int(match.group('document_id'))}:{int(match.group('version'))}"
    )


def build_notification_reply_markup(
    notification: Notification,
) -> InlineKeyboardMarkup | None:
    """Attach only safe, client-facing navigation to durable notifications.

    Notification rows stay transport-agnostic and keep their existing durable
    retry/dedupe contract. Buttons are derived at delivery time from immutable
    event metadata. Any document-specific action is snapshot-validated again by
    the Telegram handler before an upload state is opened.
    """

    recipient = str(notification.recipient_type or notification.title or "").strip()
    if recipient != "client":
        return None

    event_code = str(notification.event_code or "").strip().upper()
    if event_code in {"DOCUMENT_REUPLOAD_REQUESTED", "DOCUMENT_REJECTED"}:
        callback_data = _document_reupload_callback(notification)
        if callback_data:
            return _markup(
                ("🔁 Загрузить новую версию", callback_data),
                ("📄 Все документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
            )
        return _markup(
            ("📄 Открыть документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
        )

    if event_code == "DOCUMENT_APPROVED":
        return _markup(
            ("📄 Открыть документы", "documents_open"),
            ("📁 Следующий шаг по делу", "my_case_open"),
        )

    if event_code == "M1_CASE_ACCEPTED":
        return _markup(
            ("📝 Открыть договор", "contract_open"),
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос", "message_create"),
        )

    if event_code == "STAFF_MESSAGE_REPLIED":
        return _markup(
            ("💬 Читать ответ", "message_history"),
            ("✉️ Ответить", "message_create"),
            ("📁 Моё дело", "my_case_open"),
        )

    return None
