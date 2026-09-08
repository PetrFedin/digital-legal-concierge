from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus
from app.models.audit_log import AuditLog
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

# Case.updated_at is deliberately not a client-inactivity clock: assignment,
# reconciliation, SLA and other staff/system writes may touch the Case while the
# client has been inactive. The current stage entry is already an auditable fact,
# so derive its quiet-period anchor from the same Case history instead of adding
# another mutable timestamp truth.
_STAGE_EVENT_ACTIONS = (
    "CASE_CREATED",
    "CASE_STATUS_CHANGED",
    "CASE_TRANSFERRED_TO_M1",
    "CASE_TRANSFERRED_TO_M2",
)


def _utc(value: datetime) -> datetime:
    """Normalize DB datetimes without depending on the application host timezone."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ClientInactivityReminderService:
    """Emit one re-engagement reminder per stable client/stage snapshot."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)

    async def _current_stage_anchors(self, cases: list[Case]) -> dict[int, datetime]:
        """Return the latest audit time that established each Case's current status.

        Current product Case creation/transitions always write Case history in the
        same transaction. A legacy record without such evidence falls back to
        ``updated_at`` conservatively so we delay rather than send a premature
        reminder from an unverifiable stage timestamp.
        """

        if not cases:
            return {}

        current_status = {int(case.id): str(case.status) for case in cases}
        case_ids = tuple(current_status)
        events = (
            await self.db.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id.in_(case_ids),
                    AuditLog.action.in_(_STAGE_EVENT_ACTIONS),
                )
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            )
        ).scalars().all()

        anchors: dict[int, datetime] = {}
        for event in events:
            case_id = int(event.entity_id or 0)
            if case_id <= 0 or case_id in anchors or event.created_at is None:
                continue
            new_value = event.new_value if isinstance(event.new_value, dict) else {}
            if str(new_value.get("status") or "") != current_status.get(case_id):
                continue
            anchors[case_id] = _utc(event.created_at)

        for case in cases:
            case_id = int(case.id)
            if case_id in anchors:
                continue
            fallback = case.updated_at or case.created_at or case.last_client_action_at
            if fallback is not None:
                anchors[case_id] = _utc(fallback)
        return anchors

    async def run(self, *, now: datetime | None = None) -> int:
        if not settings.client_inactivity_reminders_enabled:
            return 0

        current = _utc(now or datetime.now(timezone.utc))
        hours = max(1, int(settings.client_inactivity_reminder_hours))
        cutoff = current - timedelta(hours=hours)
        rows = (
            await self.db.execute(
                select(Case, User)
                .join(User, User.id == Case.client_id)
                .where(Case.status.in_(tuple(_REMINDER_ACTIONS)))
                .where(Case.last_client_action_at.is_not(None))
                .where(Case.last_client_action_at <= cutoff)
                .where(User.is_blocked.is_(False))
                .order_by(Case.last_client_action_at.asc(), Case.id.asc())
                .limit(500)
            )
        ).all()

        stage_anchors = await self._current_stage_anchors(
            [case for case, _user in rows]
        )
        created_count = 0
        for case, user in rows:
            status = str(case.status)
            next_action = _REMINDER_ACTIONS.get(status)
            if not next_action or case.last_client_action_at is None:
                continue

            # A stage that only just became actionable for the client gets its
            # own quiet period. Generic later Case updates do not postpone it.
            stage_anchor = stage_anchors.get(int(case.id))
            if stage_anchor is not None and stage_anchor > cutoff:
                continue

            anchor = _utc(case.last_client_action_at).isoformat()
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
