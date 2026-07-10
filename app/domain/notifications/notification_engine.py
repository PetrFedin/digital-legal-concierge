from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.notifications.notification_rules import NOTIFICATION_RULES
from app.domain.notifications.notification_templates import TEMPLATES
from app.models.notification import Notification

class NotificationEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def emit(self, *, event_code: str, case_id: int | None = None, user_id: int | None = None, payload: dict | None = None) -> list[Notification]:
        rule = NOTIFICATION_RULES.get(event_code)
        if not rule:
            return []
        payload = payload or {}
        template = TEMPLATES.get(rule["template"], event_code)
        try:
            text = template.format(**payload)
        except KeyError:
            text = template
        result = []
        for recipient in rule["recipients"]:
            notification = Notification(case_id=case_id, user_id=user_id, channel="telegram", event_code=event_code, title=recipient, text=text, status="PENDING", is_sent=False)
            self.db.add(notification)
            result.append(notification)
        await self.db.flush()
        return result
