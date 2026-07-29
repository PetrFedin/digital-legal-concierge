from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.payment import Payment


class NoShowResolutionError(ValueError):
    pass


class NoShowResolutionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)

    async def route_lawyer_no_show_to_refund(
        self,
        *,
        consultation_id: int,
        admin_id: int | None,
        comment: str,
    ) -> tuple[Consultation, Payment]:
        normalized_comment = str(comment or "").strip()
        if len(normalized_comment) < 5:
            raise NoShowResolutionError(
                "Укажите комментарий к направлению на возврат"
            )

        consultation = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.id == consultation_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not consultation:
            raise LookupError("Консультация не найдена")
        if consultation.status == ConsultationStatus.CANCELLED:
            payment = (
                await self.db.execute(
                    select(Payment)
                    .where(
                        Payment.case_id == consultation.case_id,
                        Payment.payment_code
                        == PaymentCode.M2_CONSULTATION_PAYMENT,
                        Payment.status == PaymentStatus.REFUND_PENDING,
                    )
                    .order_by(Payment.id.desc())
                )
            ).scalars().first()
            if payment:
                return consultation, payment
        if consultation.status != ConsultationStatus.LAWYER_NO_SHOW:
            raise NoShowResolutionError(
                "Возврат по этой операции доступен только после неявки юриста"
            )

        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == consultation.case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise LookupError("Дело не найдено")

        payment = (
            await self.db.execute(
                select(Payment)
                .where(
                    Payment.case_id == case.id,
                    Payment.payment_code
                    == PaymentCode.M2_CONSULTATION_PAYMENT,
                    Payment.status.in_(
                        [
                            PaymentStatus.PAID,
                            PaymentStatus.REFUND_PENDING,
                        ]
                    ),
                )
                .order_by(Payment.created_at.desc(), Payment.id.desc())
                .with_for_update()
            )
        ).scalars().first()
        if not payment:
            raise NoShowResolutionError(
                "Оплаченный платёж консультации не найден"
            )
        if payment.status == PaymentStatus.REFUND_PENDING:
            return consultation, payment

        old_value = {
            "consultation_status": consultation.status,
            "slot_id": consultation.slot_id,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
            "payment_status": payment.status,
        }
        if consultation.slot_id:
            slot = (
                await self.db.execute(
                    select(ConsultationSlot)
                    .where(ConsultationSlot.id == consultation.slot_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if slot and slot.consultation_id == consultation.id:
                slot.consultation_id = None
                slot.held_by_user_id = None
                slot.hold_expires_at = None

        consultation.slot_id = None
        consultation.scheduled_at = None
        consultation.status = ConsultationStatus.CANCELLED
        payment.status = PaymentStatus.REFUND_PENDING
        case.next_action = "Обработать возврат после неявки юриста"

        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=admin_id,
            case_id=case.id,
            action="CONSULTATION_LAWYER_NO_SHOW_REFUND_REQUESTED",
            old_value=old_value,
            new_value={
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "payment_id": payment.id,
                "payment_status": payment.status,
                "next_action": case.next_action,
            },
            comment=normalized_comment,
        )
        await self.notifications.emit(
            event_code="CONSULTATION_CANCELLATION_REQUESTED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "amount": str(payment.amount),
            },
            dedupe_key=(
                f"consultation:{consultation.id}:lawyer-no-show-refund"
            ),
        )
        await self.db.flush()
        return consultation, payment
