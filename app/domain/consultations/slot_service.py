from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consultation_slot import ConsultationSlot


class SlotUnavailableError(RuntimeError):
    pass


class SlotService:
    HOLD_MINUTES = 20

    def __init__(self, db: AsyncSession):
        self.db = db

    async def release_expired_holds(self) -> None:
        now = datetime.now(timezone.utc)
        rows = (
            await self.db.execute(
                select(ConsultationSlot).where(
                    ConsultationSlot.status == "held",
                    ConsultationSlot.hold_expires_at.is_not(None),
                    ConsultationSlot.hold_expires_at < now,
                )
            )
        ).scalars().all()
        for slot in rows:
            slot.status = "available"
            slot.hold_expires_at = None
            slot.held_by_user_id = None
            slot.consultation_id = None

    async def get_available_slots(self, lawyer_id: int | None = None, limit: int = 30) -> list[ConsultationSlot]:
        await self.release_expired_holds()
        now = datetime.now(timezone.utc)
        conditions = [ConsultationSlot.status == "available", ConsultationSlot.starts_at > now]
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
            await self.db.execute(select(ConsultationSlot).where(ConsultationSlot.id == slot_id))
        ).scalars().first()

    async def hold_slot(self, slot_id: int, user_id: int, consultation_id: int) -> ConsultationSlot:
        await self.release_expired_holds()
        slot = await self.get_slot(slot_id)
        if not slot or slot.status != "available":
            raise SlotUnavailableError("Это время уже занято. Выберите другой слот.")
        slot.status = "held"
        slot.held_by_user_id = user_id
        slot.consultation_id = consultation_id
        slot.hold_expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.HOLD_MINUTES)
        await self.db.flush()
        return slot

    async def confirm_booking(self, slot_id: int, consultation_id: int) -> ConsultationSlot:
        slot = await self.get_slot(slot_id)
        if not slot or slot.consultation_id != consultation_id or slot.status not in {"held", "booked"}:
            raise SlotUnavailableError("Резерв слота не найден или истёк.")
        slot.status = "booked"
        slot.hold_expires_at = None
        await self.db.flush()
        return slot

    async def release_slot(self, slot_id: int, consultation_id: int | None = None) -> None:
        slot = await self.get_slot(slot_id)
        if not slot:
            return
        if consultation_id is not None and slot.consultation_id not in (None, consultation_id):
            return
        slot.status = "available"
        slot.hold_expires_at = None
        slot.held_by_user_id = None
        slot.consultation_id = None
        await self.db.flush()
