from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.consultations.client_schedule_service import (
    ClientConsultationScheduleService,
)
from app.domain.consultations.consultation_service import (
    ConsultationService,
    ConsultationSlotError,
)
from app.models.case import Case
from app.models.consultation import Consultation


class ConsultationBookingService:
    """Orchestrate client conflict checks around canonical hold operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.client_schedule = ClientConsultationScheduleService(db)
        self.consultations = ConsultationService(db)

    async def reserve_slot(
        self,
        *,
        consultation: Consultation,
        case: Case,
        client_id: int,
        slot_id: int,
        actor_type: str = "client",
        source: str = "telegram",
    ):
        slot = await self.consultations.slots.get_slot(slot_id)
        if slot is None:
            raise ConsultationSlotError("Слот консультации не найден.")
        await self.client_schedule.ensure_interval_available(
            client_id=client_id,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            exclude_consultation_id=consultation.id,
        )
        return await self.consultations.reserve_pre_payment_slot(
            consultation=consultation,
            case=case,
            client_id=client_id,
            slot_id=slot_id,
            actor_type=actor_type,
            source=source,
        )

    async def reserve_availability_option(
        self,
        *,
        consultation: Consultation,
        case: Case,
        client_id: int,
        availability_window_id: int,
        starts_at: datetime,
        duration_minutes: int,
        actor_type: str = "client",
        source: str = "telegram",
    ):
        await self.client_schedule.ensure_interval_available(
            client_id=client_id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=duration_minutes),
            exclude_consultation_id=consultation.id,
        )
        return await self.consultations.reserve_availability_option(
            consultation=consultation,
            case=case,
            client_id=client_id,
            availability_window_id=availability_window_id,
            starts_at=starts_at,
            duration_minutes=duration_minutes,
            actor_type=actor_type,
            source=source,
        )
