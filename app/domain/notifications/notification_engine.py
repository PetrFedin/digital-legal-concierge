from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notifications.notification_rules import NOTIFICATION_RULES
from app.domain.notifications.notification_templates import TEMPLATES
from app.models.notification import Notification


class NotificationEngine:
    """Create idempotent notification records from domain events.

    The current schema has no dedicated fingerprint column. Until one is
    introduced, the exact rendered event content is the deterministic
    deduplication key: event code, case, user, recipient label and text.
    Replaying the same transition therefore reuses the existing notification,
    while a genuinely changed date or message creates a new record.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def emit(
        self,
        *,
        event_code: str,
        case_id: int | None = None,
        user_id: int | None = None,
        payload: dict | None = None,
    ) -> list[Notification]:
        rule = NOTIFICATION_RULES.get(event_code)
        if not rule:
            return []

        values = payload or {}
        template = TEMPLATES.get(rule["template"], event_code)
        try:
            text = template.format(**values)
        except KeyError:
            text = template

        result: list[Notification] = []
        for recipient in rule["recipients"]:
            existing = (
                await self.db.execute(
                    select(Notification)
                    .where(
                        Notification.event_code == event_code,
                        Notification.case_id == case_id,
                        Notification.user_id == user_id,
                        Notification.title == recipient,
                        Notification.text == text,
                    )
                    .order_by(Notification.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                result.append(existing)
                continue

            notification = Notification(
                case_id=case_id,
                user_id=user_id,
                channel="telegram",
                event_code=event_code,
                title=recipient,
                text=text,
                status="PENDING",
                is_sent=False,
            )
            self.db.add(notification)
            result.append(notification)

        await self.db.flush()
        return result
