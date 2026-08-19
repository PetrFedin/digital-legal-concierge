from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case
from app.models.user import User


_REMINDER_ACTIONS = {
    CaseStatus.CALCULATOR_STARTED.value: (
        "завершите предварительный расчёт — введённые ответы уже сохранены"
    ),
    CaseStatus.CALCULATED.value: (
        "расчёт готов; выберите дальнейший путь M1 или M2"
    ),
    CaseStatus.CLIENT_DECISION.value: (
        "продолжите выбранный путь и подтвердите актуальное решение/согласие"
    ),
    CaseStatus.M1_DOCUMENTS_PENDING.value: (
        "продолжите загрузку обязательных документов для M1"
    ),
    CaseStatus.M1_DOCS_REQUESTED.value: (
        "юрист запросил документы; откройте замечания и добавьте нужные файлы"
    ),
    CaseStatus.M2_DESCRIPTION_PENDING.value: (
        "сохраните вопрос для юриста, чтобы продолжить запись на консультацию"
    ),
    CaseStatus.M2_DOCUMENTS_OPTIONAL.value: (
        "вопрос сохранён; документы необязательны, можно перейти к выбору времени"
    ),
    CaseStatus.M2_SLOT_PENDING.value: (
        "выберите актуальную дату и время консультации"
    ),
    CaseStatus.M2_PAYMENT_PENDING.value: (
        "завершите подтверждение выбранного времени/актуального платёжного шага"
    ),
}


class ClientInactivityReminderService:
    """Emit one re-engagement reminder per stable client/stage snapshot."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)

    async def run(self, *, now: datetime | None = None) -> int:
        if not settings.client_inactivity_reminders_enabled:
            return 0

        current = now or datetime.now(timezone.utc)
        hours = max(1, int(settings.client_inactivity_reminder_hours))
        cutoff = current - timedelta(hours=hours)
        rows = (
            await self.db.execute(
                select(Case, User)
                .join(User, User.id == Case.client_id)
                .where(Case.status.in_(tuple(_REMINDER_ACTIONS)))
                .where(Case.last_client_action_at.is_not(None))
                .where(Case.last_client_action_at <= cutoff)
                # A newly opened stage should get its own quiet period even when
                # the client's previous action is much older.
                .where(Case.updated_at <= cutoff)
                .where(User.is_blocked.is_(False))
                .order_by(Case.last_client_action_at.asc(), Case.id.asc())
                .limit(500)
            )
        ).all()

        created_count = 0
        for case, user in rows:
            status = str(case.status)
            next_action = _REMINDER_ACTIONS.get(status)
            if not next_action or case.last_client_action_at is None:
                continue
            anchor = case.last_client_action_at.astimezone(timezone.utc).isoformat()
            created = await self.notifications.emit(
                event_code="CLIENT_INACTIVITY_REMINDER",
                case_id=case.id,
                user_id=user.id,
                payload={
                    "case_number": case.case_number,
                    "next_action": next_action,
                },
                dedupe_key=(
                    f"client-inactivity:{case.id}:{status}:{anchor}"
                ),
            )
            created_count += len(created)
        return created_count


__all__ = ["ClientInactivityReminderService"]
