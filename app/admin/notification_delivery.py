from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case as sql_case
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.notification import Notification


ATTENTION_STATUSES = ("FAILED", "RETRY", "PENDING")
RETRYABLE_STATUSES = ("FAILED", "RETRY", "PENDING")
STATUS_LABELS = {
    "PENDING": "Ожидает отправки",
    "RETRY": "Повторная отправка",
    "FAILED": "Не доставлено",
    "SENT": "Доставлено",
}
RECIPIENT_LABELS = {
    "client": "Клиент",
    "lawyer": "Юрист",
    "admin": "Администратор",
}
FILTER_STATUSES = {
    "attention": ATTENTION_STATUSES,
    "failed": ("FAILED",),
    "retry": ("RETRY",),
    "pending": ("PENDING",),
    "sent": ("SENT",),
    "all": None,
}


class NotificationDeliveryError(ValueError):
    pass


def _clean_text(value: str | None, limit: int) -> str | None:
    clean = " ".join(str(value or "").split())
    if not clean:
        return None
    if len(clean) <= limit:
        return clean
    return clean[: max(1, limit - 1)].rstrip() + "…"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _recipient_label(notification: Notification) -> str:
    recipient = str(notification.recipient_type or notification.title or "").strip()
    return RECIPIENT_LABELS.get(recipient, recipient or "Получатель не определён")


def _target_recoverable(notification: Notification) -> bool:
    return bool(
        notification.target_chat_id is not None
        or notification.user_id is not None
        or notification.case_id is not None
    )


def _recommended_action(notification: Notification, now: datetime) -> str:
    status = str(notification.status)
    if status == "SENT":
        return "Доставка завершена"
    if notification.target_chat_id is None:
        if _target_recoverable(notification):
            return "Повторно определить Telegram-адрес из дела или профиля"
        return "Исправить источник уведомления: получатель не связан с системой"
    if status == "FAILED":
        return "Проверить причину и повторить отправку"
    if status == "RETRY" and notification.next_attempt_at:
        retry_at = notification.next_attempt_at
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        if retry_at > now:
            return "Ожидает автоматического повтора"
    if status in {"PENDING", "RETRY"}:
        return "Можно отправить сейчас"
    return "Проверить состояние доставки"


def serialize_notification(
    notification: Notification,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current = now or datetime.now(timezone.utc)
    next_attempt = notification.next_attempt_at
    if next_attempt and next_attempt.tzinfo is None:
        next_attempt = next_attempt.replace(tzinfo=timezone.utc)
    due_now = bool(
        str(notification.status) in {"PENDING", "RETRY"}
        and (next_attempt is None or next_attempt <= current)
    )
    target_available = notification.target_chat_id is not None
    target_recoverable = _target_recoverable(notification)
    can_retry = bool(
        str(notification.status) in RETRYABLE_STATUSES and target_recoverable
    )
    return {
        "id": notification.id,
        "case_id": notification.case_id,
        "event": notification.event_code,
        "title": _clean_text(notification.title, 120) or "Telegram-уведомление",
        "text": _clean_text(notification.text, 600) or "",
        "status": str(notification.status),
        "status_label": STATUS_LABELS.get(
            str(notification.status),
            "Статус уточняется",
        ),
        "recipient": _recipient_label(notification),
        "target_available": target_available,
        "target_recoverable": target_recoverable,
        "attempt_count": int(notification.attempt_count or 0),
        "last_error": _clean_text(notification.last_error, 500),
        "next_attempt_at": _iso(notification.next_attempt_at),
        "sent_at": _iso(notification.sent_at),
        "created_at": _iso(notification.created_at),
        "updated_at": _iso(notification.updated_at),
        "due_now": due_now,
        "can_retry": can_retry,
        "retry_label": (
            "Повторить сейчас"
            if target_available
            else "Повторно определить адрес"
        ),
        "recommended_action": _recommended_action(notification, current),
    }


class NotificationDeliveryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _count(self, statement) -> int:
        result = await self.db.execute(statement)
        return int(result.scalar_one() or 0)

    async def summary(self, *, now: datetime | None = None) -> dict[str, int]:
        current = now or datetime.now(timezone.utc)
        sent_since = current - timedelta(hours=24)
        pending = await self._count(
            select(func.count(Notification.id)).where(Notification.status == "PENDING")
        )
        retry = await self._count(
            select(func.count(Notification.id)).where(Notification.status == "RETRY")
        )
        failed = await self._count(
            select(func.count(Notification.id)).where(Notification.status == "FAILED")
        )
        sent_recent = await self._count(
            select(func.count(Notification.id))
            .where(Notification.status == "SENT")
            .where(Notification.sent_at >= sent_since)
        )
        due_now = await self._count(
            select(func.count(Notification.id))
            .where(Notification.status.in_(["PENDING", "RETRY"]))
            .where(
                or_(
                    Notification.next_attempt_at.is_(None),
                    Notification.next_attempt_at <= current,
                )
            )
            .where(
                or_(
                    Notification.target_chat_id.is_not(None),
                    Notification.user_id.is_not(None),
                    Notification.case_id.is_not(None),
                )
            )
        )
        return {
            "attention": pending + retry + failed,
            "pending": pending,
            "retry": retry,
            "failed": failed,
            "due_now": due_now,
            "sent_recent": sent_recent,
        }

    async def list_delivery(
        self,
        *,
        filter_name: str = "attention",
        limit: int = 100,
    ) -> dict[str, object]:
        normalized = str(filter_name or "attention").strip().lower()
        if normalized not in FILTER_STATUSES:
            raise NotificationDeliveryError("Неизвестный фильтр доставки")
        safe_limit = min(max(int(limit or 100), 1), 200)
        now = datetime.now(timezone.utc)
        statement = select(Notification)
        statuses = FILTER_STATUSES[normalized]
        if statuses:
            statement = statement.where(Notification.status.in_(statuses))
        priority = sql_case(
            (Notification.status == "FAILED", 0),
            (Notification.status == "RETRY", 1),
            (Notification.status == "PENDING", 2),
            else_=3,
        )
        result = await self.db.execute(
            statement.order_by(
                priority.asc(),
                Notification.next_attempt_at.asc().nullsfirst(),
                Notification.created_at.desc(),
                Notification.id.desc(),
            ).limit(safe_limit)
        )
        items = list(result.scalars().all())
        return {
            "generated_at": now.isoformat(),
            "filter": normalized,
            "summary": await self.summary(now=now),
            "count": len(items),
            "items": [serialize_notification(item, now=now) for item in items],
        }

    async def prepare_retry(
        self,
        *,
        notification_id: int,
        actor_id: int | None,
        expected_status: object | None,
        expected_updated_at: object | None,
    ) -> Notification:
        notification = (
            await self.db.execute(
                select(Notification)
                .where(Notification.id == int(notification_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not notification:
            raise NotificationDeliveryError("Уведомление не найдено")
        if str(notification.status) == "SENT":
            raise NotificationDeliveryError(
                "Уведомление уже доставлено и не может быть отправлено повторно"
            )
        if str(notification.status) not in RETRYABLE_STATUSES:
            raise NotificationDeliveryError(
                "Текущее состояние уведомления не допускает повторную отправку"
            )
        if not _target_recoverable(notification):
            raise NotificationDeliveryError(
                "Уведомление не связано с пользователем или делом; адрес получателя восстановить нельзя"
            )
        if expected_status is not None and str(notification.status) != str(
            expected_status
        ):
            raise NotificationDeliveryError(
                "Статус доставки изменился после загрузки экрана. Обновите список"
            )
        actual_updated_at = (
            notification.updated_at.isoformat() if notification.updated_at else None
        )
        if (
            expected_updated_at is not None
            and actual_updated_at != str(expected_updated_at)
        ):
            raise NotificationDeliveryError(
                "Уведомление изменилось после загрузки экрана. Обновите список"
            )

        old_value = {
            "status": str(notification.status),
            "attempt_count": int(notification.attempt_count or 0),
            "last_error": notification.last_error,
            "next_attempt_at": _iso(notification.next_attempt_at),
            "target_available": notification.target_chat_id is not None,
        }
        notification.status = "PENDING"
        notification.is_sent = False
        notification.next_attempt_at = None
        notification.sent_at = None
        self.db.add(
            AuditLog(
                actor_type="admin",
                actor_id=actor_id,
                action="TELEGRAM_NOTIFICATION_RETRY_REQUESTED",
                entity_type="notification",
                entity_id=notification.id,
                old_value=old_value,
                new_value={
                    "status": "PENDING",
                    "case_id": notification.case_id,
                    "event_code": notification.event_code,
                    "target_resolution_requested": notification.target_chat_id is None,
                },
                comment="Администратор запросил повторную Telegram-доставку",
            )
        )
        await self.db.flush()
        return notification

    async def due_ids(self, *, limit: int = 50) -> tuple[int, ...]:
        safe_limit = min(max(int(limit or 50), 1), 50)
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(Notification.id)
            .where(Notification.status.in_(["PENDING", "RETRY"]))
            .where(
                or_(
                    Notification.next_attempt_at.is_(None),
                    Notification.next_attempt_at <= now,
                )
            )
            .where(
                or_(
                    Notification.target_chat_id.is_not(None),
                    Notification.user_id.is_not(None),
                    Notification.case_id.is_not(None),
                )
            )
            .order_by(Notification.created_at.asc(), Notification.id.asc())
            .limit(safe_limit)
        )
        return tuple(int(item) for item in result.scalars().all())
