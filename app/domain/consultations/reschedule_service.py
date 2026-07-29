from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.lawyer_capacity_service import (
    LawyerCapacityError,
    LawyerCapacityService,
)
from app.domain.consultations.client_schedule_service import (
    ClientConsultationConflictError,
    ClientConsultationScheduleService,
)
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationSlotError,
)
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot


class ConsultationRescheduleService:
    """Atomically replace a paid consultation slot without changing its terms."""

    ALLOWED_STATUSES = frozenset(
        {
            ConsultationStatus.CONFIRMED.value,
            ConsultationStatus.BOOKED.value,
        }
    )

    def __init__(self, db: AsyncSession):
        self.db = db
        self.client_schedule = ClientConsultationScheduleService(db)
        self.capacity = LawyerCapacityService(db)

    async def reschedule(
        self,
        *,
        consultation: Consultation,
        case: Case,
        client_id: int,
        new_slot_id: int,
        actor_type: str = "client",
        actor_id: int | None = None,
        source: str = "telegram",
    ) -> tuple[Consultation, ConsultationSlot]:
        if case is None or case.client_id != client_id:
            raise ConsultationNotFoundError(
                "Консультация не принадлежит текущему клиенту."
            )
        if case.route not in {RouteCode.M2, RouteCode.M2.value}:
            raise ActiveConsultationConflictError(
                "Перенос доступен только для консультационного маршрута."
            )
        if consultation is None or consultation.case_id != case.id:
            raise ConsultationNotFoundError(
                "Консультация не принадлежит указанному делу."
            )
        if new_slot_id <= 0:
            raise ConsultationSlotError("Некорректный новый слот консультации.")

        # Read immutable identifiers before taking locks. All authoritative
        # checks are repeated after acquiring the global lock order below.
        candidate = await self.db.get(ConsultationSlot, new_slot_id)
        if candidate is None:
            raise ConsultationSlotError(
                "Новое время стало недоступно; прежняя запись сохранена."
            )
        expected_lawyer_id = case.assigned_lawyer_id or consultation.lawyer_id
        if expected_lawyer_id is None:
            raise ConsultationSlotError(
                "Для оплаченной консультации не определён текущий юрист."
            )
        if candidate.lawyer_id != expected_lawyer_id:
            raise ConsultationSlotError(
                "Новое время относится к другому юристу. "
                "Прежняя запись сохранена."
            )

        try:
            await self.client_schedule.ensure_interval_available(
                client_id=client_id,
                starts_at=candidate.starts_at,
                ends_at=candidate.ends_at,
                exclude_consultation_id=consultation.id,
            )
        except ClientConsultationConflictError as exc:
            raise ConsultationSlotError(
                "Новое время пересекается с другой вашей консультацией; "
                "прежняя запись сохранена."
            ) from exc

        locked_case = (
            await self.db.execute(
                select(Case)
                .where(
                    Case.id == case.id,
                    Case.client_id == client_id,
                    Case.route == RouteCode.M2.value,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_case is None:
            raise ConsultationNotFoundError("Консультационное дело не найдено.")

        try:
            await self.capacity.lock_active_lawyer(expected_lawyer_id)
        except LawyerCapacityError as exc:
            raise ConsultationSlotError(
                "Текущий юрист больше недоступен; прежняя запись сохранена."
            ) from exc

        locked_consultation = (
            await self.db.execute(
                select(Consultation)
                .where(
                    Consultation.id == consultation.id,
                    Consultation.case_id == locked_case.id,
                    Consultation.status.in_(self.ALLOWED_STATUSES),
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_consultation is None:
            raise ActiveConsultationConflictError(
                "Перенос недоступен на текущем этапе консультации."
            )
        if locked_consultation.slot_id is None:
            raise ConsultationSlotError(
                "Текущий подтверждённый слот консультации не найден."
            )

        slot_ids = sorted({locked_consultation.slot_id, new_slot_id})
        locked_slots = list(
            (
                await self.db.execute(
                    select(ConsultationSlot)
                    .where(ConsultationSlot.id.in_(slot_ids))
                    .order_by(ConsultationSlot.id.asc())
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        slots_by_id = {slot.id: slot for slot in locked_slots}
        old_slot = slots_by_id.get(locked_consultation.slot_id)
        new_slot = slots_by_id.get(new_slot_id)
        if (
            old_slot is None
            or old_slot.consultation_id != locked_consultation.id
            or old_slot.status != "booked"
        ):
            raise ConsultationSlotError(
                "Текущий подтверждённый слот консультации повреждён."
            )
        if old_slot.id == new_slot_id:
            return locked_consultation, old_slot

        now = datetime.now(timezone.utc)
        if (
            new_slot is None
            or new_slot.status != "available"
            or self._as_utc(new_slot.starts_at) <= now
            or new_slot.consultation_id is not None
        ):
            raise ConsultationSlotError(
                "Новое время стало недоступно; прежняя запись сохранена."
            )
        if new_slot.lawyer_id != expected_lawyer_id:
            raise ConsultationSlotError(
                "Новое время относится к другому юристу. "
                "Прежняя запись сохранена."
            )

        old_duration = self._duration_minutes(old_slot)
        new_duration = self._duration_minutes(new_slot)
        if new_duration != old_duration:
            raise ConsultationSlotError(
                "Изменение длительности оплаченной консультации требует "
                "отдельного перерасчёта; прежняя запись сохранена."
            )

        overlapping = (
            await self.db.execute(
                select(ConsultationSlot.id)
                .where(
                    ConsultationSlot.lawyer_id == new_slot.lawyer_id,
                    ConsultationSlot.id.notin_({old_slot.id, new_slot.id}),
                    ConsultationSlot.starts_at < new_slot.ends_at,
                    ConsultationSlot.ends_at > new_slot.starts_at,
                    or_(
                        ConsultationSlot.status == "booked",
                        and_(
                            ConsultationSlot.status == "held",
                            ConsultationSlot.hold_expires_at.is_not(None),
                            ConsultationSlot.hold_expires_at > now,
                        ),
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if overlapping is not None:
            raise ConsultationSlotError(
                "Новое время конфликтует с другой записью; "
                "прежняя запись сохранена."
            )

        old_snapshot = {
            "slot_id": old_slot.id,
            "lawyer_id": old_slot.lawyer_id,
            "scheduled_at": old_slot.starts_at.isoformat(),
            "duration_minutes": old_duration,
        }
        try:
            async with self.db.begin_nested():
                old_slot.status = "available"
                old_slot.hold_expires_at = None
                old_slot.held_by_user_id = None
                old_slot.consultation_id = None
                locked_consultation.slot_id = None
                await self.db.flush()

                new_slot.status = "booked"
                new_slot.hold_expires_at = None
                new_slot.held_by_user_id = client_id
                new_slot.consultation_id = locked_consultation.id
                locked_consultation.slot_id = new_slot.id
                locked_consultation.lawyer_id = new_slot.lawyer_id
                locked_consultation.scheduled_at = new_slot.starts_at
                locked_consultation.status = ConsultationStatus.BOOKED.value
                locked_case.route = RouteCode.M2.value
                locked_case.status = CaseStatus.M2_CONSULTATION_BOOKED.value
                locked_case.next_action = "Ожидайте консультации в выбранное время"
                await add_case_history_event(
                    self.db,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    case_id=locked_case.id,
                    action="CONSULTATION_RESCHEDULED",
                    old_value=old_snapshot,
                    new_value={
                        "consultation_id": locked_consultation.id,
                        "slot_id": new_slot.id,
                        "lawyer_id": new_slot.lawyer_id,
                        "scheduled_at": new_slot.starts_at.isoformat(),
                        "duration_minutes": new_duration,
                        "source": source,
                    },
                )
                await NotificationEngine(self.db).emit(
                    event_code="CONSULTATION_RESCHEDULED",
                    case_id=locked_case.id,
                    user_id=client_id,
                    payload={"date": new_slot.starts_at.isoformat()},
                )
                await self.db.flush()
        except IntegrityError as exc:
            raise ConsultationSlotError(
                "Новое время стало недоступно; прежняя запись сохранена."
            ) from exc

        return locked_consultation, new_slot

    @classmethod
    def _duration_minutes(cls, slot: ConsultationSlot) -> int:
        return max(
            1,
            int(
                (cls._as_utc(slot.ends_at) - cls._as_utc(slot.starts_at)).total_seconds()
                // 60
            ),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
