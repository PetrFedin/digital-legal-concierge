from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.slot_service import SlotService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.payment import Payment


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ConsultationRefundService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.slots = SlotService(db)
        self.notifications = NotificationEngine(db)

    async def _lock_consultation(self, consultation_id: int) -> Consultation:
        consultation = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.id == consultation_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not consultation:
            raise LookupError("Консультация не найдена")
        return consultation

    async def _lock_paid_consultation_payment(self, case_id: int) -> Payment:
        payment = (
            await self.db.execute(
                select(Payment)
                .where(
                    Payment.case_id == case_id,
                    Payment.payment_code
                    == PaymentCode.M2_CONSULTATION_PAYMENT,
                    Payment.status.in_(
                        [
                            PaymentStatus.PAID,
                            PaymentStatus.REFUND_PENDING,
                            PaymentStatus.REFUNDED,
                        ]
                    ),
                )
                .order_by(Payment.created_at.desc(), Payment.id.desc())
                .with_for_update()
            )
        ).scalars().first()
        if not payment:
            raise ValueError(
                "Оплаченный платёж по консультации не найден. "
                "Отмена требует проверки администратора."
            )
        return payment

    async def request_cancellation(
        self,
        *,
        consultation: Consultation,
        case: Case,
        client_id: int,
        reason: str | None = None,
    ) -> tuple[Consultation, Payment]:
        consultation = await self._lock_consultation(consultation.id)
        payment = await self._lock_paid_consultation_payment(case.id)

        if (
            consultation.status == ConsultationStatus.CANCELLED
            and payment.status
            in {PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED}
        ):
            return consultation, payment

        if consultation.status != ConsultationStatus.BOOKED:
            raise ValueError(
                "Заявку на возврат можно создать только для "
                "оплаченной подтверждённой консультации"
            )
        if not consultation.slot_id:
            raise ValueError("У консультации отсутствует подтверждённый слот")

        slot = await self.slots.get_slot_for_update(consultation.slot_id)
        if (
            not slot
            or slot.status != "booked"
            or slot.consultation_id != consultation.id
        ):
            raise ValueError(
                "Связь консультации со слотом повреждена. "
                "Требуется ручная проверка администратора."
            )
        if as_utc(slot.starts_at) <= datetime.now(timezone.utc):
            raise ValueError(
                "Нельзя отменить консультацию после её начала. "
                "Свяжитесь с администратором."
            )

        old_payment_status = payment.status
        old_consultation = {
            "status": consultation.status,
            "slot_id": consultation.slot_id,
            "lawyer_id": consultation.lawyer_id,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
        }
        slot_snapshot = {
            "slot_id": slot.id,
            "lawyer_id": slot.lawyer_id,
            "starts_at": slot.starts_at.isoformat(),
            "ends_at": slot.ends_at.isoformat(),
        }

        await self.slots.release_slot(slot.id, consultation.id)
        consultation.slot_id = None
        consultation.scheduled_at = None
        consultation.status = ConsultationStatus.CANCELLED
        payment.status = PaymentStatus.REFUND_PENDING

        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=client_id,
            case_id=case.id,
            action="CONSULTATION_CANCELLATION_REQUESTED",
            old_value={
                "consultation": old_consultation,
                "payment_status": old_payment_status,
            },
            new_value={
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "payment_id": payment.id,
                "payment_status": payment.status,
                "released_slot": slot_snapshot,
                "reason": (reason or "").strip() or None,
            },
            comment=(
                "Клиент отменил оплаченную консультацию. "
                "Требуется ручная обработка возврата."
            ),
        )
        await self.notifications.emit(
            event_code="CONSULTATION_CANCELLATION_REQUESTED",
            case_id=case.id,
            user_id=client_id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "amount": str(payment.amount),
            },
        )
        await self.db.flush()
        return consultation, payment

    async def resolve_refund(
        self,
        *,
        payment_id: int,
        decision: str,
        actor_id: int | None,
        comment: str,
    ) -> Payment:
        normalized_decision = str(decision or "").strip().lower()
        normalized_comment = str(comment or "").strip()
        if normalized_decision not in {"refunded", "declined"}:
            raise ValueError("Решение должно быть refunded или declined")
        if len(normalized_comment) < 5:
            raise ValueError("Укажите комментарий к решению")

        payment = (
            await self.db.execute(
                select(Payment)
                .where(Payment.id == payment_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not payment:
            raise LookupError("Платёж не найден")
        if payment.payment_code != PaymentCode.M2_CONSULTATION_PAYMENT:
            raise ValueError("Этот платёж не относится к консультации")

        if normalized_decision == "refunded" and payment.status == PaymentStatus.REFUNDED:
            return payment
        if normalized_decision == "declined" and payment.status == PaymentStatus.PAID:
            return payment
        if payment.status != PaymentStatus.REFUND_PENDING:
            raise ValueError(
                "Платёж не находится в статусе ожидания решения по возврату"
            )

        case = await self.db.get(Case, payment.case_id)
        if not case:
            raise LookupError("Дело не найдено")

        old_status = payment.status
        payment.status = (
            PaymentStatus.REFUNDED
            if normalized_decision == "refunded"
            else PaymentStatus.PAID
        )
        action = (
            "CONSULTATION_REFUND_COMPLETED"
            if normalized_decision == "refunded"
            else "CONSULTATION_REFUND_DECLINED"
        )
        event_code = (
            "CONSULTATION_REFUNDED"
            if normalized_decision == "refunded"
            else "CONSULTATION_REFUND_DECLINED"
        )
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action=action,
            old_value={"payment_id": payment.id, "status": old_status},
            new_value={
                "payment_id": payment.id,
                "status": payment.status,
                "decision": normalized_decision,
            },
            comment=normalized_comment,
        )
        await self.notifications.emit(
            event_code=event_code,
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "amount": str(payment.amount),
                "comment": normalized_comment,
            },
        )
        await self.db.flush()
        return payment
