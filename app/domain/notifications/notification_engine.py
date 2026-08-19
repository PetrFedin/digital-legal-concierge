from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notifications.notification_rules import NOTIFICATION_RULES
from app.domain.notifications.notification_template_extensions import TEMPLATE_EXTENSIONS
from app.domain.notifications.notification_templates import TEMPLATES
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.user import User


@dataclass(frozen=True)
class NotificationTarget:
    recipient_type: str
    target_chat_id: int | None
    user_id: int | None = None
    label: str | None = None


class NotificationEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _case(self, case_id: int | None) -> Case | None:
        if case_id is None:
            return None
        return await self.db.get(Case, case_id)

    async def _client_target(
        self,
        *,
        case_id: int | None,
        user_id: int | None,
    ) -> NotificationTarget:
        client = await self.db.get(User, user_id) if user_id else None
        if client is None:
            case = await self._case(case_id)
            client = await self.db.get(User, case.client_id) if case else None
        return NotificationTarget(
            recipient_type="client",
            target_chat_id=client.telegram_id if client else None,
            user_id=client.id if client else user_id,
            label="client",
        )

    async def _lawyer_target(
        self,
        *,
        case_id: int | None,
    ) -> NotificationTarget:
        lawyer_id: int | None = None
        case = await self._case(case_id)
        if case and case.assigned_lawyer_id:
            lawyer_id = case.assigned_lawyer_id
        elif case_id is not None:
            consultation = (
                await self.db.execute(
                    select(Consultation)
                    .where(Consultation.case_id == case_id)
                    .where(Consultation.lawyer_id.is_not(None))
                    .order_by(
                        Consultation.scheduled_at.desc(),
                        Consultation.created_at.desc(),
                    )
                    .limit(1)
                )
            ).scalars().first()
            lawyer_id = consultation.lawyer_id if consultation else None

        lawyer = await self.db.get(Lawyer, lawyer_id) if lawyer_id else None
        return NotificationTarget(
            recipient_type="lawyer",
            target_chat_id=lawyer.telegram_id if lawyer else None,
            label="lawyer",
        )

    async def _admin_targets(self) -> list[NotificationTarget]:
        admins = (
            await self.db.execute(
                select(AdminUser)
                .where(AdminUser.is_active.is_(True))
                .where(AdminUser.telegram_id.is_not(None))
                .order_by(AdminUser.id.asc())
            )
        ).scalars().all()
        if not admins:
            return [
                NotificationTarget(
                    recipient_type="admin",
                    target_chat_id=None,
                    label="admin",
                )
            ]
        return [
            NotificationTarget(
                recipient_type="admin",
                target_chat_id=admin.telegram_id,
                label=f"admin:{admin.id}",
            )
            for admin in admins
        ]

    async def _targets(
        self,
        recipient: str,
        *,
        case_id: int | None,
        user_id: int | None,
    ) -> list[NotificationTarget]:
        if recipient == "client":
            return [
                await self._client_target(case_id=case_id, user_id=user_id)
            ]
        if recipient == "lawyer":
            return [await self._lawyer_target(case_id=case_id)]
        if recipient == "admin":
            return await self._admin_targets()
        return [
            NotificationTarget(
                recipient_type=recipient,
                target_chat_id=None,
                user_id=user_id,
                label=recipient,
            )
        ]

    @staticmethod
    def _scoped_dedupe_key(
        base_key: str | None,
        target: NotificationTarget,
    ) -> str | None:
        if not base_key:
            return None
        target_part = (
            str(target.target_chat_id)
            if target.target_chat_id is not None
            else "unroutable"
        )
        return f"{base_key}:{target.recipient_type}:{target_part}"

    async def emit(
        self,
        *,
        event_code: str,
        case_id: int | None = None,
        user_id: int | None = None,
        payload: dict | None = None,
        dedupe_key: str | None = None,
    ) -> list[Notification]:
        rule = NOTIFICATION_RULES.get(event_code)
        if not rule:
            return []

        payload = payload or {}
        template_key = rule["template"]
        template = TEMPLATE_EXTENSIONS.get(
            template_key,
            TEMPLATES.get(template_key, event_code),
        )
        try:
            text = template.format(**payload)
        except KeyError:
            text = template

        created: list[Notification] = []
        for recipient in rule["recipients"]:
            targets = await self._targets(
                recipient,
                case_id=case_id,
                user_id=user_id,
            )
            for target in targets:
                scoped_key = self._scoped_dedupe_key(dedupe_key, target)
                if scoped_key:
                    existing = (
                        await self.db.execute(
                            select(Notification).where(
                                Notification.dedupe_key == scoped_key
                            )
                        )
                    ).scalars().first()
                    if existing:
                        continue

                notification = Notification(
                    case_id=case_id,
                    user_id=target.user_id,
                    channel="telegram",
                    event_code=event_code,
                    recipient_type=target.recipient_type,
                    target_chat_id=target.target_chat_id,
                    dedupe_key=scoped_key,
                    title=target.label or recipient,
                    text=text,
                    status="PENDING",
                    is_sent=False,
                    attempt_count=0,
                )
                self.db.add(notification)
                created.append(notification)

        await self.db.flush()
        return created
