from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationSlotError,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer


class ConsultationRescheduleService:
    """Atomically replace a confirmed consultation slot.

    The existing booked slot is kept until every ownership, status, lawyer and
    availability check for the replacement has passed. The actual swap runs in
    a savepoint so a failed flush restores the original booking.
    """

    ALLOWED_STATUSES = frozenset(
        {
            ConsultationStatus.CONFIRMED.value,
            ConsultationStatus.BOOKED.value,
        }
    )

    def __init__(self, db: AsyncSession):
        self.db = db

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
        if consultation is None or consultation.case_id != case.id:
            raise ConsultationNotFoundError(
                "Консультация не принадлежит указанному делу."
            )

        locked_consultation = (
            await self.db.execute(
                select(Consultation)
                .where(
                    Consultation.id == consultation.id,
                    Consultation.case_id == case.id,
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

        old_slot = (
            await self.db.execute(
                select(ConsultationSlot)
                .where(
                    ConsultationSlot.id == locked_consultation.slot_id,
                    ConsultationSlot.consultation_id == locked_consultation.id,
                    ConsultationSlot.status == "booked",
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if old_slot is None:
            raise ConsultationSlotError(
                "Текущий подтверждённый слот консультации повреждён."
            )
        if old_slot.id == new_slot_id:
            return locked_consultation, old_slot

        new_slot = (
            await self.db.execute(
                select(ConsultationSlot)
                .where(ConsultationSlot.id == new_slot_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
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

        lawyer = (
            await self.db.execute(
                select(Lawyer).where(
                    Lawyer.id == new_slot.lawyer_id,
                    Lawyer.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if lawyer is None:
            raise ConsultationSlotError(
                "Юрист выбранного времени сейчас недоступен."
            )
        expected_lawyer_id = case.assigned_lawyer_id or locked_consultation.lawyer_id
        if expected_lawyer_id is not None and new_slot.lawyer_id != expected_lawyer_id:
            raise ConsultationSlotError(
                "Новое время относится к другому юристу. "
                "Прежняя запись сохранена."
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
                await add_case_history_event(
                    self.db,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    case_id=case.id,
                    action="CONSULTATION_RESCHEDULED",
                    old_value=old_snapshot,
                    new_value={
                        "consultation_id": locked_consultation.id,
                        "slot_id": new_slot.id,
                        "lawyer_id": new_slot.lawyer_id,
                        "scheduled_at": new_slot.starts_at.isoformat(),
                        "source": source,
                    },
                )
                await self.db.flush()
        except IntegrityError as exc:
            raise ConsultationSlotError(
                "Новое время стало недоступно; прежняя запись сохранена."
            ) from exc

        return locked_consultation, new_slot

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
