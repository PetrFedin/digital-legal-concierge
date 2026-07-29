from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.lawyer_capacity_service import (
    LawyerCapacityError,
    LawyerCapacityService,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer


class SlotUnavailableError(RuntimeError):
    pass


class SlotService:
    HOLD_MINUTES = 20

    def __init__(self, db: AsyncSession):
        self.db = db

    async def release_expired_holds(self) -> None:
        """Release expired holds and restore linked M2 entities coherently."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(ConsultationSlot)
            .where(
                ConsultationSlot.status == "held",
                ConsultationSlot.hold_expires_at.is_not(None),
                ConsultationSlot.hold_expires_at < now,
            )
            .order_by(ConsultationSlot.id.asc())
            .with_for_update()
        )
        expired_slots = list(result.scalars().all())

        for slot in expired_slots:
            consultation = None
            if slot.consultation_id is not None:
                consultation = await self.db.get(Consultation, slot.consultation_id)

            if consultation is not None and consultation.slot_id == slot.id:
                previous_consultation_status = consultation.status
                consultation.slot_id = None
                consultation.lawyer_id = None
                consultation.scheduled_at = None

                try:
                    current_status = ConsultationStatus(previous_consultation_status)
                except (TypeError, ValueError):
                    current_status = None

                case = await self.db.get(Case, consultation.case_id)
                resettable_statuses = {
                    ConsultationStatus.SLOT_PENDING,
                    ConsultationStatus.SLOT_RESERVED,
                    ConsultationStatus.PAYMENT_PENDING,
                }
                if current_status in resettable_statuses:
                    consultation.status = ConsultationStatus.SLOT_PENDING.value
                    if case is not None:
                        previous_case_status = case.status
                        case.route = RouteCode.M2.value
                        case.status = CaseStatus.M2_SLOT_PENDING.value
                        case.next_action = "Выберите удобное время консультации"
                        await add_case_history_event(
                            self.db,
                            actor_type="system",
                            actor_id=None,
                            case_id=case.id,
                            action="CONSULTATION_SLOT_HOLD_EXPIRED",
                            old_value={
                                "consultation_id": consultation.id,
                                "consultation_status": previous_consultation_status,
                                "case_status": previous_case_status,
                                "slot_id": slot.id,
                            },
                            new_value={
                                "consultation_id": consultation.id,
                                "consultation_status": consultation.status,
                                "case_status": case.status,
                                "slot_id": None,
                            },
                        )

            slot.status = "available"
            slot.hold_expires_at = None
            slot.held_by_user_id = None
            slot.consultation_id = None

        if expired_slots:
            await self.db.flush()

    async def get_available_slots(
        self,
        lawyer_id: int | None = None,
        limit: int = 30,
    ) -> list[ConsultationSlot]:
        await self.release_expired_holds()
        now = datetime.now(timezone.utc)
        inactive_lawyer_ids = select(Lawyer.id).where(Lawyer.is_active.is_(False))
        conditions = [
            ConsultationSlot.status == "available",
            ConsultationSlot.starts_at > now,
            ~ConsultationSlot.lawyer_id.in_(inactive_lawyer_ids),
        ]
        if lawyer_id is not None:
            conditions.append(ConsultationSlot.lawyer_id == lawyer_id)
        result = await self.db.execute(
            select(ConsultationSlot)
            .where(and_(*conditions))
            .order_by(ConsultationSlot.starts_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_slot(self, slot_id: int) -> ConsultationSlot | None:
        await self.release_expired_holds()
        return (
            await self.db.execute(
                select(ConsultationSlot).where(ConsultationSlot.id == slot_id)
            )
        ).scalars().first()

    async def hold_slot(
        self,
        slot_id: int,
        user_id: int,
        consultation_id: int,
    ) -> ConsultationSlot:
        await self.release_expired_holds()
        now = datetime.now(timezone.utc)

        # Read identifiers first, then lock in the global order
        # Case -> Lawyer -> Consultation -> Slot. This matches case assignment
        # and prevents cross-flow deadlocks under PostgreSQL.
        candidate = (
            await self.db.execute(
                select(ConsultationSlot).where(ConsultationSlot.id == slot_id)
            )
        ).scalar_one_or_none()
        if candidate is None:
            raise SlotUnavailableError("Слот не найден.")
        consultation = (
            await self.db.execute(
                select(Consultation).where(Consultation.id == consultation_id)
            )
        ).scalar_one_or_none()
        if consultation is not None:
            locked_case_id = (
                await self.db.execute(
                    select(Case.id)
                    .where(Case.id == consultation.case_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_case_id is None:
                raise SlotUnavailableError("Дело консультации не найдено.")

        try:
            await LawyerCapacityService(self.db).ensure_available(
                lawyer_id=candidate.lawyer_id,
                exclude_case_id=(consultation.case_id if consultation is not None else None),
            )
        except LawyerCapacityError as exc:
            raise SlotUnavailableError(
                "У выбранного юриста больше нет свободной capacity. "
                "Выберите другое время или другого юриста."
            ) from exc

        locked_consultation = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.id == consultation_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalars().first()

        existing_hold = (
            await self.db.execute(
                select(ConsultationSlot)
                .where(
                    ConsultationSlot.consultation_id == consultation_id,
                    ConsultationSlot.status.in_({"held", "booked"}),
                )
                .order_by(ConsultationSlot.id.asc())
                .with_for_update()
            )
        ).scalars().first()

        if existing_hold is not None:
            if (
                existing_hold.id == slot_id
                and existing_hold.status == "held"
                and existing_hold.held_by_user_id == user_id
                and existing_hold.hold_expires_at is not None
                and self._as_utc(existing_hold.hold_expires_at) > now
            ):
                return existing_hold
            raise SlotUnavailableError(
                "За консультацией уже удерживается другой слот."
            )

        if locked_consultation is not None and locked_consultation.slot_id is not None:
            raise SlotUnavailableError(
                "За консультацией уже удерживается другой слот."
            )

        hold_expires_at = now + timedelta(minutes=self.HOLD_MINUTES)
        inactive_lawyer_ids = select(Lawyer.id).where(Lawyer.is_active.is_(False))

        try:
            async with self.db.begin_nested():
                result = await self.db.execute(
                    update(ConsultationSlot)
                    .where(
                        ConsultationSlot.id == slot_id,
                        ConsultationSlot.status == "available",
                        ConsultationSlot.starts_at > now,
                        ~ConsultationSlot.lawyer_id.in_(inactive_lawyer_ids),
                    )
                    .values(
                        status="held",
                        held_by_user_id=user_id,
                        consultation_id=consultation_id,
                        hold_expires_at=hold_expires_at,
                    )
                )
                if result.rowcount != 1:
                    raise SlotUnavailableError(
                        "Это время уже занято или недоступно. Выберите другой слот."
                    )
                await self.db.flush()
        except IntegrityError as exc:
            raise SlotUnavailableError(
                "Это время уже занято или для консультации выбран другой слот."
            ) from exc

        slot = (
            await self.db.execute(
                select(ConsultationSlot)
                .where(ConsultationSlot.id == slot_id)
                .execution_options(populate_existing=True)
            )
        ).scalars().first()
        if not slot:
            raise SlotUnavailableError("Слот не найден.")
        return slot

    async def confirm_booking(
        self,
        slot_id: int,
        consultation_id: int,
    ) -> ConsultationSlot:
        result = await self.db.execute(
            update(ConsultationSlot)
            .where(
                ConsultationSlot.id == slot_id,
                ConsultationSlot.consultation_id == consultation_id,
                ConsultationSlot.status == "held",
                ConsultationSlot.hold_expires_at >= datetime.now(timezone.utc),
            )
            .values(status="booked", hold_expires_at=None)
        )
        if result.rowcount != 1:
            existing = await self.get_slot(slot_id)
            if (
                not existing
                or existing.consultation_id != consultation_id
                or existing.status != "booked"
            ):
                raise SlotUnavailableError("Резерв слота не найден или истёк.")
        await self.db.flush()
        slot = await self.get_slot(slot_id)
        if not slot:
            raise SlotUnavailableError("Слот не найден.")
        return slot

    async def release_slot(
        self,
        slot_id: int,
        consultation_id: int | None = None,
    ) -> None:
        conditions = [ConsultationSlot.id == slot_id]
        if consultation_id is not None:
            conditions.append(ConsultationSlot.consultation_id == consultation_id)
        await self.db.execute(
            update(ConsultationSlot)
            .where(*conditions)
            .values(
                status="available",
                hold_expires_at=None,
                held_by_user_id=None,
                consultation_id=None,
            )
        )
        await self.db.flush()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
