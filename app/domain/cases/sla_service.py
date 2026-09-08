from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.notifications.notification_engine import NotificationEngine
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.system.settings_service import SettingsService


SLA_NOT_STARTED = "NOT_STARTED"
SLA_FIRST_RESPONSE_PENDING = "FIRST_RESPONSE_PENDING"
SLA_FIRST_RESPONSE_OVERDUE = "FIRST_RESPONSE_OVERDUE"
SLA_ACTION_PENDING = "ACTION_PENDING"
SLA_ACTION_OVERDUE = "ACTION_OVERDUE"
SLA_PAUSED = "PAUSED"
SLA_CLOSED = "CLOSED"

CLOSED_CASE_STATUSES = {
    "CLOSED",
    "ARCHIVED",
    "CANCELLED",
    "COMPLETED",
    "M1_CLOSED",
    "M2_CLOSED",
}

PAUSED_CASE_STATUSES = {
    "M1_DOCS_REQUESTED",
    "M1_REJECTED",
    "M1_WAITING_PAYMENT_30000",
    "M1_POWER_OF_ATTORNEY",
    "M1_WAITING_30_DAYS",
    "M1_WAITING_PAYMENT_70000",
    "M1_ENFORCEMENT",
    "M1_WAITING_SUCCESS_FEE",
    "M2_PAYMENT_PENDING",
    "M2_CONSULTATION_BOOKED",
}


class CaseSLAError(ValueError):
    pass


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class CaseSLAService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = SettingsService(db)
        self.notifications = NotificationEngine(db)

    async def _setting_hours(
        self,
        key: str,
        *,
        default: int,
        minimum: int = 1,
        maximum: int = 720,
    ) -> int:
        try:
            raw = await self.settings.get_value(key)
            value = int(raw)
        except (KeyError, TypeError, ValueError):
            value = default
        return min(max(value, minimum), maximum)

    async def _first_response_hours(self) -> int:
        return await self._setting_hours(
            "sla.first_lawyer_response_hours",
            default=4,
        )

    async def _next_action_hours(self) -> int:
        return await self._setting_hours(
            "sla.next_lawyer_action_hours",
            default=48,
        )

    async def _repeat_hours(self) -> int:
        return await self._setting_hours(
            "sla.escalation_repeat_hours",
            default=4,
            maximum=168,
        )

    async def _lock_case(self, case_id: int) -> Case:
        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise LookupError("Дело не найдено")
        return case

    @staticmethod
    def _snapshot(case: Case) -> dict:
        return {
            "assigned_lawyer_id": case.assigned_lawyer_id,
            "assigned_at": (
                case.assigned_at.isoformat() if case.assigned_at else None
            ),
            "first_lawyer_response_at": (
                case.first_lawyer_response_at.isoformat()
                if case.first_lawyer_response_at
                else None
            ),
            "last_lawyer_activity_at": (
                case.last_lawyer_activity_at.isoformat()
                if case.last_lawyer_activity_at
                else None
            ),
            "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
            "sla_status": case.sla_status,
            "escalation_level": int(case.escalation_level or 0),
            "case_status": case.status,
        }

    async def start_assignment_sla(
        self,
        *,
        case: Case,
        lawyer_id: int,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
    ) -> Case:
        now = datetime.now(timezone.utc)
        old = self._snapshot(case)
        response_hours = await self._first_response_hours()

        case.assigned_lawyer_id = lawyer_id
        case.assigned_at = now
        case.first_lawyer_response_at = None
        case.last_lawyer_activity_at = None
        case.sla_due_at = now + timedelta(hours=response_hours)
        case.sla_status = SLA_FIRST_RESPONSE_PENDING
        case.escalation_level = 0

        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_SLA_STARTED",
            old_value=old,
            new_value=self._snapshot(case),
            comment=comment or "Запущен SLA первой реакции после назначения юриста",
        )
        await self.db.flush()
        return case

    async def clear_assignment_sla(
        self,
        *,
        case: Case,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
    ) -> Case:
        old = self._snapshot(case)
        case.assigned_lawyer_id = None
        case.assigned_at = None
        case.first_lawyer_response_at = None
        case.last_lawyer_activity_at = None
        case.sla_due_at = None
        case.sla_status = SLA_NOT_STARTED
        case.escalation_level = 0
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_SLA_CLEARED",
            old_value=old,
            new_value=self._snapshot(case),
            comment=comment or "SLA очищен после снятия назначения",
        )
        await self.db.flush()
        return case

    async def record_lawyer_activity(
        self,
        *,
        case: Case,
        lawyer_id: int,
        action: str,
        comment: str | None = None,
    ) -> Case:
        if case.assigned_lawyer_id != lawyer_id:
            raise CaseSLAError(
                "SLA-действие доступно только назначенному юристу"
            )

        now = datetime.now(timezone.utc)
        old = self._snapshot(case)
        if case.first_lawyer_response_at is None:
            case.first_lawyer_response_at = now
        case.last_lawyer_activity_at = now
        case.escalation_level = 0

        normalized_status = str(case.status).upper()
        if normalized_status in CLOSED_CASE_STATUSES:
            case.sla_status = SLA_CLOSED
            case.sla_due_at = None
        elif normalized_status in PAUSED_CASE_STATUSES:
            case.sla_status = SLA_PAUSED
            case.sla_due_at = None
        else:
            action_hours = await self._next_action_hours()
            case.sla_status = SLA_ACTION_PENDING
            case.sla_due_at = now + timedelta(hours=action_hours)

        await add_case_history_event(
            self.db,
            actor_type="lawyer",
            actor_id=lawyer_id,
            case_id=case.id,
            action="CASE_SLA_LAWYER_ACTIVITY",
            old_value=old,
            new_value={**self._snapshot(case), "lawyer_action": action},
            comment=comment,
        )
        await self.db.flush()
        return case

    async def synchronize_case_status(
        self,
        *,
        case: Case,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
    ) -> Case:
        if case.assigned_lawyer_id is None:
            return case
        normalized_status = str(case.status).upper()
        target_status: str | None = None
        if normalized_status in CLOSED_CASE_STATUSES:
            target_status = SLA_CLOSED
        elif normalized_status in PAUSED_CASE_STATUSES:
            target_status = SLA_PAUSED
        elif case.first_lawyer_response_at is not None:
            target_status = SLA_ACTION_PENDING

        if target_status is None or target_status == case.sla_status:
            return case

        old = self._snapshot(case)
        case.sla_status = target_status
        if target_status in {SLA_CLOSED, SLA_PAUSED}:
            case.sla_due_at = None
        elif target_status == SLA_ACTION_PENDING:
            action_hours = await self._next_action_hours()
            case.sla_due_at = datetime.now(timezone.utc) + timedelta(
                hours=action_hours
            )
        case.escalation_level = 0
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_SLA_STATUS_SYNCHRONIZED",
            old_value=old,
            new_value=self._snapshot(case),
            comment=comment,
        )
        await self.db.flush()
        return case

    async def acknowledge_overdue(
        self,
        *,
        case_id: int,
        actor_id: int | None,
        comment: str,
        expected_sla_status: str | None = None,
        expected_escalation_level: int | None = None,
    ) -> Case:
        normalized_comment = str(comment or "").strip()
        if len(normalized_comment) < 5:
            raise CaseSLAError("Укажите комментарий к продлению SLA")
        case = await self._lock_case(case_id)
        if case.assigned_lawyer_id is None:
            raise CaseSLAError("У дела нет назначенного юриста")
        if case.sla_status not in {
            SLA_FIRST_RESPONSE_OVERDUE,
            SLA_ACTION_OVERDUE,
        }:
            raise CaseSLAError("Дело не находится в просроченном SLA")

        if expected_sla_status is not None:
            normalized_expected_status = str(expected_sla_status).strip().upper()
            if normalized_expected_status != str(case.sla_status).upper():
                raise CaseSLAError(
                    "SLA изменился после загрузки экрана. Обновите список и повторите решение"
                )
        if expected_escalation_level is not None:
            try:
                normalized_expected_level = int(expected_escalation_level)
            except (TypeError, ValueError) as error:
                raise CaseSLAError("Некорректный ожидаемый уровень эскалации") from error
            if normalized_expected_level != int(case.escalation_level or 0):
                raise CaseSLAError(
                    "Уровень эскалации изменился после загрузки экрана. Обновите список"
                )

        old = self._snapshot(case)
        now = datetime.now(timezone.utc)
        if case.first_lawyer_response_at is None:
            case.sla_status = SLA_FIRST_RESPONSE_PENDING
            hours = await self._first_response_hours()
        else:
            case.sla_status = SLA_ACTION_PENDING
            hours = await self._next_action_hours()
        case.sla_due_at = now + timedelta(hours=hours)
        case.escalation_level = 0
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_SLA_ACKNOWLEDGED",
            old_value=old,
            new_value=self._snapshot(case),
            comment=normalized_comment,
        )
        await self.notifications.emit(
            event_code="CASE_SLA_ACKNOWLEDGED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "due_at": case.sla_due_at.strftime("%d.%m.%Y %H:%M UTC"),
            },
            dedupe_key=(
                f"case:{case.id}:sla-ack:{int(now.timestamp())}"
            ),
        )
        await self.db.flush()
        return case

    async def escalate_overdue_cases(self, limit: int = 200) -> dict:
        now = datetime.now(timezone.utc)
        cases = list(
            (
                await self.db.execute(
                    select(Case)
                    .where(
                        Case.assigned_lawyer_id.is_not(None),
                        Case.sla_due_at.is_not(None),
                        Case.sla_due_at <= now,
                        Case.sla_status.in_(
                            [
                                SLA_FIRST_RESPONSE_PENDING,
                                SLA_ACTION_PENDING,
                                SLA_FIRST_RESPONSE_OVERDUE,
                                SLA_ACTION_OVERDUE,
                            ]
                        ),
                        Case.status.notin_(tuple(CLOSED_CASE_STATUSES)),
                    )
                    .order_by(Case.sla_due_at.asc(), Case.id.asc())
                    .limit(min(max(int(limit), 1), 1000))
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
        )

        repeat_hours = await self._repeat_hours()
        escalated: list[dict] = []
        for case in cases:
            old = self._snapshot(case)
            is_first = case.first_lawyer_response_at is None
            case.sla_status = (
                SLA_FIRST_RESPONSE_OVERDUE if is_first else SLA_ACTION_OVERDUE
            )
            case.escalation_level = int(case.escalation_level or 0) + 1
            overdue_due_at = as_utc(case.sla_due_at)
            case.sla_due_at = now + timedelta(hours=repeat_hours)

            lawyer = await self.db.get(Lawyer, case.assigned_lawyer_id)
            event_code = (
                "CASE_SLA_FIRST_RESPONSE_OVERDUE"
                if is_first
                else "CASE_SLA_ACTION_OVERDUE"
            )
            await add_case_history_event(
                self.db,
                actor_type="system",
                actor_id=None,
                case_id=case.id,
                action=event_code,
                old_value=old,
                new_value=self._snapshot(case),
                comment=(
                    "Автоматическая эскалация просроченного SLA, "
                    f"уровень {case.escalation_level}"
                ),
            )
            await self.notifications.emit(
                event_code=event_code,
                case_id=case.id,
                payload={
                    "case_number": case.case_number,
                    "lawyer": lawyer.full_name if lawyer else "не определён",
                    "due_at": overdue_due_at.strftime("%d.%m.%Y %H:%M UTC"),
                    "level": case.escalation_level,
                },
                dedupe_key=(
                    f"case:{case.id}:sla:{event_code}:"
                    f"{case.escalation_level}:{int(overdue_due_at.timestamp())}"
                ),
            )
            escalated.append(
                {
                    "case_id": case.id,
                    "case_number": case.case_number,
                    "lawyer_id": case.assigned_lawyer_id,
                    "sla_status": case.sla_status,
                    "escalation_level": case.escalation_level,
                }
            )

        await self.db.flush()
        return {
            "examined": len(cases),
            "escalated_count": len(escalated),
            "escalated": escalated,
        }