from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select, update
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
        await self.db.execute(
            update(ConsultationSlot)
            .where(
                ConsultationSlot.status == "held",
                ConsultationSlot.hold_expires_at.is_not(None),
                ConsultationSlot.hold_expires_at < now,
            )
            .values(
                status="available",
                hold_expires_at=None,
                held_by_user_id=None,
                consultation_id=None,
            )
        )

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
        hold_expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.HOLD_MINUTES)
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

    async def confirm_booking(self, slot_id: int, consultation_id: int) -> ConsultationSlot:
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
            if not existing or existing.consultation_id != consultation_id or existing.status != "booked":
                raise SlotUnavailableError("Резерв слота не найден или истёк.")
        await self.db.flush()
        slot = await self.get_slot(slot_id)
        if not slot:
            raise SlotUnavailableError("Слот не найден.")
        return slot

    async def release_slot(self, slot_id: int, consultation_id: int | None = None) -> None:
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
