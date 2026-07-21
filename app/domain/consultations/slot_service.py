from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot


class SlotUnavailableError(RuntimeError):
    pass


class SlotService:
    HOLD_MINUTES = 20

    def __init__(self, db: AsyncSession):
        self.db = db

    async def release_expired_holds(self) -> None:
        """Release expired holds and restore linked M2 entities coherently.

        A slot hold, its consultation and its case form one logical reservation.
        Releasing only the slot leaves ``Consultation.slot_id`` behind and can
        prevent the slot from being assigned again.  Keep all three records in
        the same transaction; the caller remains responsible for committing.
        """
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
        conditions = [
            ConsultationSlot.status == "available",
            ConsultationSlot.starts_at > now,
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
        hold_expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=self.HOLD_MINUTES
        )
        result = await self.db.execute(
            update(ConsultationSlot)
            .where(
                ConsultationSlot.id == slot_id,
                ConsultationSlot.status == "available",
            )
            .values(
                status="held",
                held_by_user_id=user_id,
                consultation_id=consultation_id,
                hold_expires_at=hold_expires_at,
            )
        )
        if result.rowcount != 1:
            raise SlotUnavailableError("Это время уже занято. Выберите другой слот.")
        await self.db.flush()
        slot = await self.get_slot(slot_id)
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
