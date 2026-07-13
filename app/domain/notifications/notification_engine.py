from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notifications.notification_rules import NOTIFICATION_RULES
from app.domain.notifications.notification_templates import TEMPLATES
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.notification import Notification


class NotificationEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def emit(
        self,
        *,
        event_code: str,
        case_id: int | None = None,
        user_id: int | None = None,
        admin_user_id: int | None = None,
        payload: dict | None = None,
        scheduled_at: datetime | None = None,
        dedup_suffix: str | None = None,
        action_label: str | None = None,
        action_callback: str | None = None,
        channel: str = "telegram",
    ) -> list[Notification]:
        rule = NOTIFICATION_RULES.get(event_code)
        if not rule:
            return []

        payload = payload or {}
        text = self._render(rule["template"], event_code, payload)
        recipients = await self._resolve_recipients(
            rule.get("recipients", []),
            case_id=case_id,
            explicit_user_id=user_id,
            explicit_admin_user_id=admin_user_id,
        )
        result: list[Notification] = []
        for recipient_type, recipient_user_id, recipient_admin_user_id in recipients:
            dedup_key = self._dedup_key(
                event_code=event_code,
                case_id=case_id,
                recipient_type=recipient_type,
                user_id=recipient_user_id,
                admin_user_id=recipient_admin_user_id,
                suffix=dedup_suffix,
            )
            existing = (
                await self.db.execute(
                    select(Notification).where(Notification.dedup_key == dedup_key)
                )
            ).scalars().first()
            if existing:
                continue

            notification = Notification(
                case_id=case_id,
                user_id=recipient_user_id,
                admin_user_id=recipient_admin_user_id,
                recipient_type=recipient_type,
                channel=channel,
                event_code=event_code,
                title=self._title(event_code, recipient_type),
                text=text,
                action_label=action_label,
                action_callback=action_callback,
                dedup_key=dedup_key,
                status="SCHEDULED" if scheduled_at and scheduled_at > datetime.now(timezone.utc) else "PENDING",
                scheduled_at=scheduled_at,
                is_sent=False,
                is_read=False,
            )
            self.db.add(notification)
            result.append(notification)

        if result:
            await self.db.flush()
        return result

    async def _resolve_recipients(
        self,
        recipient_types: list[str],
        *,
        case_id: int | None,
        explicit_user_id: int | None,
        explicit_admin_user_id: int | None,
    ) -> list[tuple[str, int | None, int | None]]:
        case = await self.db.get(Case, case_id) if case_id else None
        resolved: list[tuple[str, int | None, int | None]] = []

        for recipient_type in recipient_types:
            if recipient_type == "client":
                target_user_id = explicit_user_id or (case.client_id if case else None)
                if target_user_id:
                    resolved.append(("client", target_user_id, None))

            elif recipient_type == "lawyer":
                if explicit_admin_user_id:
                    resolved.append(("lawyer", None, explicit_admin_user_id))
                    continue
                if case and case.assigned_lawyer_id:
                    lawyer = await self.db.get(Lawyer, case.assigned_lawyer_id)
                    if lawyer and lawyer.admin_user_id:
                        resolved.append(("lawyer", None, lawyer.admin_user_id))

            elif recipient_type == "admin":
                if explicit_admin_user_id:
                    resolved.append(("admin", None, explicit_admin_user_id))
                    continue
                rows = (
                    await self.db.execute(
                        select(AdminUser).where(
                            AdminUser.is_active.is_(True),
                            AdminUser.role.like("%admin%"),
                        )
                    )
                ).scalars().all()
                resolved.extend(("admin", None, admin.id) for admin in rows)

            elif recipient_type == "operator":
                rows = (
                    await self.db.execute(
                        select(AdminUser).where(
                            AdminUser.is_active.is_(True),
                            AdminUser.role.like("%operator%"),
                        )
                    )
                ).scalars().all()
                resolved.extend(("operator", None, operator.id) for operator in rows)

        # Keep deterministic order and remove duplicates.
        return list(dict.fromkeys(resolved))

    @staticmethod
    def _render(template_key: str, event_code: str, payload: dict) -> str:
        template = TEMPLATES.get(template_key, event_code)
        class SafeDict(dict):
            def __missing__(self, key):
                return "—"
        return template.format_map(SafeDict(payload))

    @staticmethod
    def _title(event_code: str, recipient_type: str) -> str:
        prefixes = {
            "client": "Действие по делу",
            "lawyer": "Рабочее уведомление",
            "admin": "Контрольное уведомление",
            "operator": "Операционное уведомление",
        }
        return f"{prefixes.get(recipient_type, 'Уведомление')}: {event_code}"

    @staticmethod
    def _dedup_key(
        *,
        event_code: str,
        case_id: int | None,
        recipient_type: str,
        user_id: int | None,
        admin_user_id: int | None,
        suffix: str | None,
    ) -> str:
        recipient = f"u:{user_id}" if user_id else f"a:{admin_user_id}"
        return ":".join(
            [
                event_code,
                f"case:{case_id or 0}",
                recipient_type,
                recipient,
                suffix or "default",
            ]
        )
