from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notifications.notification_engine import NotificationEngine
from app.models.case import Case


# Functional/UX specification reminder cadence for unfinished client actions.
# Only statuses that are unambiguously client-owned are included here.
# Payment reminders and consultation-booking reminders keep their dedicated
# services so this job cannot duplicate provider/slot-specific behaviour.
REMINDER_POLICY: dict[str, tuple[tuple[int, str], ...]] = {
    "CALCULATOR_STARTED": (
        (24, "Продолжите предварительный расчет — введенные данные сохранены."),
    ),
    "CALCULATED": (
        (24, "Расчет сохранен. Вы можете продолжить и передать данные юристу."),
        (72, "Расчет сохранен. Если вопрос актуален, продолжите оформление дела."),
        (168, "Расчет по-прежнему сохранен и доступен в «Моем деле»."),
    ),
    "CLIENT_DECISION": (
        (24, "Расчет сохранен. Выберите дальнейший шаг по обращению."),
        (72, "Обращение ожидает вашего решения. Данные не потеряны."),
        (168, "Вы можете вернуться к сохраненному обращению и продолжить."),
    ),
    "M1_DOCUMENTS_PENDING": (
        (24, "Для проверки дела загрузите ДДУ и доступные приложения."),
        (72, "Юрист сможет начать проверку после загрузки документов."),
        (168, "Документы еще не получены. Обращение и расчет сохранены."),
    ),
    "M1_DOCS_REQUESTED": (
        (24, "Юрист ожидает запрошенные документы по вашему делу."),
        (72, "Запрошенные документы еще не получены."),
        (168, "Загрузите запрошенные документы, когда они будут готовы."),
    ),
    "M2_SLOT_PENDING": (
        (24, "Для продолжения консультационного маршрута выберите свободное время."),
    ),
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ClientInactivityReminderService:
    """Emit at most one currently-due reminder per stable Case snapshot.

    The Case status/updated_at pair is the stable snapshot. A later domain
    update creates a new snapshot and therefore a new reminder cycle. This
    avoids notification storms while preserving the 24h/72h/7d cadence.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)

    async def run(self, *, now: datetime | None = None) -> int:
        current = _as_utc(now or datetime.now(timezone.utc))
        cases = (
            await self.db.execute(
                select(Case)
                .where(Case.status.in_(tuple(REMINDER_POLICY)))
                .order_by(Case.updated_at.asc(), Case.id.asc())
            )
        ).scalars().all()

        created_count = 0
        for case in cases:
            anchor = _as_utc(case.updated_at or case.created_at)
            age_hours = max((current - anchor).total_seconds() / 3600, 0.0)
            due = [
                (hours, message)
                for hours, message in REMINDER_POLICY.get(str(case.status), ())
                if age_hours >= hours
            ]
            if not due:
                continue

            # If the scheduler was unavailable for a long period, send only
            # the latest due reminder rather than a burst of historical ones.
            hours, message = due[-1]
            snapshot = anchor.isoformat()
            created = await self.notifications.emit(
                event_code="CLIENT_INACTIVITY_REMINDER",
                case_id=case.id,
                payload={
                    "case_number": case.case_number,
                    "message": message,
                    "next_action": case.next_action or "Откройте «Мое дело»",
                },
                dedupe_key=(
                    f"case:{case.id}:client-inactivity:"
                    f"{case.status}:{snapshot}:{hours}h"
                ),
            )
            if created:
                created_count += 1

        return created_count
