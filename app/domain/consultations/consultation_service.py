from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation


TERMINAL_CONSULTATION_STATUSES = (
    ConsultationStatus.DONE,
    ConsultationStatus.CLIENT_NO_SHOW,
    ConsultationStatus.LAWYER_NO_SHOW,
    ConsultationStatus.CANCELLED,
    ConsultationStatus.CLOSED,
    ConsultationStatus.RESCHEDULED,
)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ConsultationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.slots = SlotService(db)
        self.notifications = NotificationEngine(db)

    async def get_current_for_case(self, case_id: int) -> Consultation | None:
        result = await self.db.execute(
            select(Consultation)
            .where(Consultation.case_id == case_id)
            .where(
                Consultation.status.notin_(
                    [status.value for status in TERMINAL_CONSULTATION_STATUSES]
                )
            )
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
        )
        return result.scalars().first()

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

    async def _validate_related_case(
        self,
        *,
        client_id: int,
        subject_type: str,
        related_case_id: int | None,
    ) -> int | None:
        if subject_type != "existing_case":
            return None
        if related_case_id is None:
            raise ValueError("Выберите дело, к которому относится консультация")
        related_case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == related_case_id)
                .where(Case.client_id == client_id)
            )
        ).scalar_one_or_none()
        if not related_case:
            raise ValueError(
                "Выбранное дело не найдено или принадлежит другому клиенту"
            )
        return related_case.id

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
        verified_related_case_id = await self._validate_related_case(
            client_id=client_id,
            subject_type=subject_type,
            related_case_id=related_case_id,
        )
        consultation.client_description = description
        consultation.subject_type = subject_type
        consultation.related_case_id = verified_related_case_id
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
                "related_case_id": verified_related_case_id,
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
            or as_utc(slot.hold_expires_at) < now
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

    async def reschedule_booked(
        self,
        *,
        consultation: Consultation,
        case,
        client_id: int,
        new_slot_id: int,
    ):
        locked_consultation = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.id == consultation.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not locked_consultation:
            raise ValueError("Консультация не найдена")
        consultation = locked_consultation

        if consultation.status != ConsultationStatus.BOOKED:
            raise ValueError(
                "Перенос без повторной оплаты доступен только для подтверждённой консультации"
            )
        if not consultation.slot_id:
            raise ValueError("У консультации отсутствует текущий слот")
        if consultation.slot_id == new_slot_id:
            slot = await self.slots.get_slot(new_slot_id)
            if (
                slot
                and slot.status == "booked"
                and slot.consultation_id == consultation.id
            ):
                return consultation, slot
            raise SlotUnavailableError("Текущий слот не найден")

        old_slot = await self.slots.get_slot_for_update(consultation.slot_id)
        if (
            not old_slot
            or old_slot.status != "booked"
            or old_slot.consultation_id != consultation.id
        ):
            raise ValueError(
                "Текущая подтверждённая бронь повреждена. Обратитесь к администратору."
            )
        if as_utc(old_slot.starts_at) <= datetime.now(timezone.utc):
            raise ValueError(
                "Нельзя перенести консультацию, которая уже началась или завершилась"
            )

        old_snapshot = {
            "slot_id": old_slot.id,
            "lawyer_id": old_slot.lawyer_id,
            "starts_at": old_slot.starts_at.isoformat(),
            "ends_at": old_slot.ends_at.isoformat(),
        }
        old_date_text = old_slot.starts_at.strftime("%d.%m.%Y %H:%M")

        old_slot.status = "rescheduling"
        old_slot.consultation_id = None
        old_slot.held_by_user_id = None
        old_slot.hold_expires_at = None
        await self.db.flush()

        new_slot = await self.slots.book_available_slot(
            slot_id=new_slot_id,
            user_id=client_id,
            consultation_id=consultation.id,
        )

        old_slot.status = "available"
        consultation.slot_id = new_slot.id
        consultation.lawyer_id = new_slot.lawyer_id
        consultation.scheduled_at = new_slot.starts_at
        consultation.status = ConsultationStatus.BOOKED
        new_snapshot = {
            "slot_id": new_slot.id,
            "lawyer_id": new_slot.lawyer_id,
            "starts_at": new_slot.starts_at.isoformat(),
            "ends_at": new_slot.ends_at.isoformat(),
        }
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=client_id,
            case_id=case.id,
            action="CONSULTATION_RESCHEDULED",
            old_value=old_snapshot,
            new_value=new_snapshot,
            comment="Клиент перенёс оплаченную консультацию без повторной оплаты",
        )
        await self.notifications.emit(
            event_code="CONSULTATION_RESCHEDULED",
            case_id=case.id,
            user_id=client_id,
            payload={
                "case_number": case.case_number,
                "old_date": old_date_text,
                "new_date": new_slot.starts_at.strftime("%d.%m.%Y %H:%M"),
            },
        )
        await self.db.flush()
        return consultation, new_slot

    async def cancel(
        self,
        *,
        consultation,
        case,
        actor_type: str,
        actor_id: int | None,
        comment: str,
    ):
        if consultation.status == ConsultationStatus.BOOKED:
            from app.domain.payments.refund_service import (
                ConsultationRefundService,
            )

            cancelled, _payment = await ConsultationRefundService(
                self.db
            ).request_cancellation(
                consultation=consultation,
                case=case,
                client_id=actor_id or 0,
                reason=comment,
            )
            return cancelled

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
