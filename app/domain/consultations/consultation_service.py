from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.consultation import Consultation


class ConsultationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.slots = SlotService(db)

    async def get_current_for_case(self, case_id: int) -> Consultation | None:
        result = await self.db.execute(
            select(Consultation)
            .where(Consultation.case_id == case_id)
            .order_by(Consultation.created_at.desc())
        )
        consultation = result.scalars().first()
        if consultation and consultation.status not in {
            ConsultationStatus.DONE,
            ConsultationStatus.CANCELLED,
            ConsultationStatus.CLOSED,
        }:
            return consultation
        return None

    async def get_or_create_for_case(self, case):
        consultation = await self.get_current_for_case(case.id)
        if consultation:
            return consultation
        consultation = Consultation(
            case_id=case.id,
            status=ConsultationStatus.DESCRIPTION_PENDING,
        )
        self.db.add(consultation)
        await self.db.flush()
        return consultation

    async def save_description(
        self,
        *,
        consultation,
        case,
        client_id: int,
        description: str,
        subject_type: str = "new_or_other",
        related_case_id: int | None = None,
    ):
        consultation.client_description = description
        consultation.subject_type = subject_type
        consultation.related_case_id = related_case_id
        if consultation.status != ConsultationStatus.BOOKED:
            consultation.status = ConsultationStatus.DOCUMENTS_OPTIONAL
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=client_id,
            case_id=case.id,
            action="CONSULTATION_DESCRIPTION_SAVED",
            new_value={
                "description": description,
                "subject_type": subject_type,
                "related_case_id": related_case_id,
            },
        )
        await self.db.flush()
        return consultation

    async def reserve_slot(self, *, consultation, case, client_id: int, slot_id: int):
        previous_slot_id = consultation.slot_id
        if previous_slot_id == slot_id:
            slot = await self.slots.get_slot(slot_id)
            if (
                slot
                and slot.consultation_id == consultation.id
                and slot.status in {"held", "booked"}
            ):
                return consultation, slot
        slot = await self.slots.hold_slot(slot_id, client_id, consultation.id)
        if previous_slot_id and previous_slot_id != slot.id:
            await self.slots.release_slot(previous_slot_id, consultation.id)
        consultation.slot_id = slot.id
        consultation.lawyer_id = slot.lawyer_id
        consultation.scheduled_at = slot.starts_at
        consultation.status = ConsultationStatus.PAYMENT_PENDING
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=client_id,
            case_id=case.id,
            action="CONSULTATION_SLOT_HELD",
            new_value={
                "slot_id": slot.id,
                "lawyer_id": slot.lawyer_id,
                "scheduled_at": slot.starts_at.isoformat(),
                "hold_expires_at": (
                    slot.hold_expires_at.isoformat()
                    if slot.hold_expires_at
                    else None
                ),
            },
        )
        await self.db.flush()
        return consultation, slot

    async def require_payable_slot(self, consultation: Consultation):
        await self.slots.release_expired_holds()
        await self.db.refresh(consultation)
        if consultation.status != ConsultationStatus.PAYMENT_PENDING:
            raise SlotUnavailableError(
                "Запись уже не ожидает оплату. Выберите новое свободное время."
            )
        if not consultation.slot_id:
            raise SlotUnavailableError(
                "Резерв времени истёк. Выберите новый слот."
            )
        slot = await self.slots.get_slot(consultation.slot_id)
        now = datetime.now(timezone.utc)
        if (
            not slot
            or slot.status != "held"
            or slot.consultation_id != consultation.id
            or slot.hold_expires_at is None
            or slot.hold_expires_at < now
        ):
            raise SlotUnavailableError(
                "Резерв времени истёк. Выберите новый слот."
            )
        return slot

    async def mark_booked_after_payment(self, *, consultation, case):
        if consultation.status == ConsultationStatus.BOOKED:
            if not consultation.slot_id:
                raise SlotUnavailableError(
                    "Оплата получена, но слот не связан с консультацией."
                )
            slot = await self.slots.get_slot(consultation.slot_id)
            if (
                slot
                and slot.status == "booked"
                and slot.consultation_id == consultation.id
            ):
                return consultation
            raise SlotUnavailableError(
                "Оплата получена, но бронь требует ручной проверки."
            )

        await self.require_payable_slot(consultation)
        slot = await self.slots.confirm_booking(
            consultation.slot_id,
            consultation.id,
        )
        consultation.status = ConsultationStatus.BOOKED
        consultation.lawyer_id = slot.lawyer_id
        consultation.scheduled_at = slot.starts_at
        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=case.id,
            action="CONSULTATION_BOOKED_AFTER_PAYMENT",
            new_value={
                "consultation_id": consultation.id,
                "slot_id": slot.id,
                "lawyer_id": slot.lawyer_id,
                "scheduled_at": slot.starts_at.isoformat(),
            },
        )
        await self.db.flush()
        return consultation

    async def cancel(
        self,
        *,
        consultation,
        case,
        actor_type: str,
        actor_id: int | None,
        comment: str,
    ):
        if consultation.slot_id:
            await self.slots.release_slot(
                consultation.slot_id,
                consultation.id,
            )
        consultation.slot_id = None
        consultation.scheduled_at = None
        consultation.status = ConsultationStatus.CANCELLED
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CONSULTATION_CANCELLED",
            new_value={"consultation_id": consultation.id},
            comment=comment,
        )
        await self.db.flush()
        return consultation

    async def mark_done(
        self,
        *,
        consultation,
        case,
        lawyer_id: int,
        result: str,
        decision: str,
    ):
        consultation.status = ConsultationStatus.DONE
        consultation.lawyer_id = lawyer_id
        consultation.lawyer_result = result
        consultation.decision = decision
        await self.db.flush()
        return consultation
