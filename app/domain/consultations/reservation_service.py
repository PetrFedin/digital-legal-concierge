from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationSlotError,
)
from app.domain.consultations.slot_service import SlotService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import OPEN_PAYMENT_STATUSES, PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.payment import Payment


class ConsultationReservationService:
    """Release a temporary consultation hold before payment safely.

    A provider payment link can remain payable outside the application. For
    that reason the client may release a reservation only while the
    consultation is still in ``SLOT_RESERVED`` and no open or paid M2 payment
    exists. Once payment preparation starts, a manager or the payment
    lifecycle must resolve the reservation instead of silently detaching it.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.slots = SlotService(db)

    async def release_before_payment(
        self,
        *,
        consultation: Consultation,
        case: Case,
        client_id: int,
        actor_type: str = "client",
        actor_id: int | None = None,
        source: str = "telegram",
        reason: str = "Клиент освободил временный резерв",
    ) -> Consultation:
        if consultation is None or case is None:
            raise ConsultationNotFoundError("Консультация не найдена.")
        if consultation.case_id != case.id or case.client_id != client_id:
            raise ConsultationNotFoundError(
                "Консультация не принадлежит текущему клиенту."
            )

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

        locked = (
            await self.db.execute(
                select(Consultation)
                .where(
                    Consultation.id == consultation.id,
                    Consultation.case_id == locked_case.id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            raise ConsultationNotFoundError("Консультация не найдена.")

        try:
            current = ConsultationStatus(locked.status)
        except (TypeError, ValueError) as exc:
            raise ActiveConsultationConflictError(
                "Неизвестный статус консультации не допускает освобождение резерва."
            ) from exc

        # Repeating the action after a successful release is idempotent.
        if current == ConsultationStatus.SLOT_PENDING and locked.slot_id is None:
            return locked
        if current != ConsultationStatus.SLOT_RESERVED:
            raise ActiveConsultationConflictError(
                "Резерв нельзя освободить после перехода к оплате."
            )
        if locked.slot_id is None:
            raise ConsultationSlotError("У консультации отсутствует удерживаемый слот.")

        protected_payment = (
            await self.db.execute(
                select(Payment)
                .where(
                    Payment.case_id == locked_case.id,
                    Payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT.value,
                    Payment.status.in_(
                        set(OPEN_PAYMENT_STATUSES) | {PaymentStatus.PAID.value}
                    ),
                )
                .order_by(Payment.id.asc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if protected_payment is not None:
            raise ActiveConsultationConflictError(
                "Платёж уже создан или подтверждён. Для изменения времени "
                "обратитесь к менеджеру."
            )

        slot = (
            await self.db.execute(
                select(ConsultationSlot)
                .where(
                    ConsultationSlot.id == locked.slot_id,
                    ConsultationSlot.consultation_id == locked.id,
                    ConsultationSlot.status == "held",
                    ConsultationSlot.held_by_user_id == client_id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if slot is None:
            raise ConsultationSlotError(
                "Удерживаемый слот не найден или принадлежит другой консультации."
            )

        previous_case_status = locked_case.status
        previous_slot_id = slot.id
        previous_scheduled_at = locked.scheduled_at
        previous_lawyer_id = locked.lawyer_id

        await self.slots.release_slot(slot.id, locked.id)
        locked.slot_id = None
        locked.lawyer_id = None
        locked.scheduled_at = None
        locked.status = ConsultationStatus.SLOT_PENDING.value
        locked_case.route = RouteCode.M2.value
        locked_case.status = CaseStatus.M2_SLOT_PENDING.value
        locked_case.next_action = "Выберите удобное время консультации"

        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=locked_case.id,
            action="CONSULTATION_SLOT_RELEASED_BY_CLIENT",
            old_value={
                "consultation_id": locked.id,
                "consultation_status": current.value,
                "case_status": previous_case_status,
                "slot_id": previous_slot_id,
                "lawyer_id": previous_lawyer_id,
                "scheduled_at": (
                    previous_scheduled_at.isoformat()
                    if previous_scheduled_at is not None
                    else None
                ),
            },
            new_value={
                "consultation_id": locked.id,
                "consultation_status": locked.status,
                "case_status": locked_case.status,
                "slot_id": None,
                "source": source,
            },
            comment=reason,
        )
        await self.db.flush()
        return locked
