from sqlalchemy import select

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.payment import Payment


class PaymentWebhookService:
    def __init__(self, db):
        self.db = db
        self.payments = PaymentService(db)
        self.cases = CaseService(db)
        self.notifications = NotificationEngine(db)

    async def _lock_payment(self, payment_id: int) -> Payment:
        payment = (
            await self.db.execute(
                select(Payment)
                .where(Payment.id == payment_id)
                .with_for_update()
            )
        ).scalar_one()
        return payment

    async def _mark_consultation_payment_review(
        self,
        *,
        payment: Payment,
        case,
        reason: str,
        provider_payload: dict | None,
    ) -> Payment:
        old_status = payment.status
        payment.status = PaymentStatus.PAID_REVIEW
        await add_case_history_event(
            self.db,
            actor_type="payment_provider",
            actor_id=None,
            case_id=case.id,
            action="CONSULTATION_PAYMENT_REVIEW_REQUIRED",
            old_value={"status": old_status},
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "status": payment.status,
                "reason": reason,
                "payload": provider_payload or {},
            },
            comment=(
                "Деньги получены, но автоматическое подтверждение слота "
                "невозможно. Требуется ручная проверка администратора."
            ),
        )
        await self.notifications.emit(
            event_code="CONSULTATION_PAYMENT_REVIEW",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "reason": reason,
            },
        )
        await self.db.flush()
        return payment

    async def process_successful_payment(
        self,
        *,
        payment,
        case,
        provider_payload=None,
    ):
        payment = await self._lock_payment(payment.id)

        if payment.status == PaymentStatus.PAID_REVIEW:
            return payment

        if payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
            consultation_service = ConsultationService(self.db)
            consultation = await consultation_service.get_current_for_case(case.id)

            if not consultation or not consultation.slot_id:
                return await self._mark_consultation_payment_review(
                    payment=payment,
                    case=case,
                    reason="Активная консультация или связанный слот не найдены",
                    provider_payload=provider_payload,
                )

            expected_reservation_key = PaymentService.consultation_reservation_key(
                consultation.id,
                consultation.slot_id,
            )
            if payment.reservation_key != expected_reservation_key:
                return await self._mark_consultation_payment_review(
                    payment=payment,
                    case=case,
                    reason=(
                        "Оплачена устаревшая ссылка, относящаяся к другому резерву "
                        "или ранее выбранному времени"
                    ),
                    provider_payload=provider_payload,
                )

            if payment.status == PaymentStatus.PAID:
                if consultation.status == ConsultationStatus.BOOKED:
                    return payment
                return await self._mark_consultation_payment_review(
                    payment=payment,
                    case=case,
                    reason="Платёж уже отмечен оплаченным, но подтверждённая консультация не найдена",
                    provider_payload=provider_payload,
                )

            try:
                await consultation_service.mark_booked_after_payment(
                    consultation=consultation,
                    case=case,
                )
            except (SlotUnavailableError, ValueError) as error:
                return await self._mark_consultation_payment_review(
                    payment=payment,
                    case=case,
                    reason=str(error),
                    provider_payload=provider_payload,
                )

            await self.payments.mark_paid(
                payment=payment,
                case=case,
                actor_type="payment_provider",
            )
            if case.status != CaseStatus.M2_CONSULTATION_BOOKED:
                await self.cases.change_status(
                    case=case,
                    next_status=CaseStatus.M2_CONSULTATION_BOOKED,
                    actor_type="system",
                    actor_id=None,
                    force=True,
                    comment="Консультация подтверждена после оплаты",
                )
            await self.notifications.emit(
                event_code="M2_CONSULTATION_BOOKED",
                case_id=case.id,
                payload={
                    "case_number": case.case_number,
                    "date": (
                        consultation.scheduled_at.strftime("%d.%m.%Y %H:%M")
                        if consultation.scheduled_at
                        else "уточняется"
                    ),
                },
            )
        else:
            if payment.status == PaymentStatus.PAID:
                return payment
            await self.payments.mark_paid(
                payment=payment,
                case=case,
                actor_type="payment_provider",
            )
            mapping = {
                PaymentCode.M1_INITIAL_PAYMENT: [
                    CaseStatus.M1_PAYMENT_30000_RECEIVED,
                    CaseStatus.M1_POWER_OF_ATTORNEY,
                ],
                PaymentCode.M1_COURT_PAYMENT: [
                    CaseStatus.M1_PAYMENT_70000_RECEIVED,
                    CaseStatus.M1_ENFORCEMENT,
                ],
                PaymentCode.M1_SUCCESS_FEE: [
                    CaseStatus.M1_SUCCESS_FEE_RECEIVED,
                    CaseStatus.M1_CLOSED,
                ],
            }
            for status in mapping.get(payment.payment_code, []):
                await self.cases.change_status(
                    case=case,
                    next_status=status,
                    actor_type="system",
                    actor_id=None,
                    force=True,
                    comment=f"Автопереход после оплаты {payment.payment_code}",
                )

        await add_case_history_event(
            self.db,
            actor_type="payment_provider",
            actor_id=None,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_PROCESSED",
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "payload": provider_payload or {},
            },
        )
        await self.db.flush()
        return payment

    async def process_failed_payment(
        self,
        *,
        payment,
        case,
        provider_payload=None,
    ):
        payment = await self._lock_payment(payment.id)
        if payment.status in {
            PaymentStatus.PAID,
            PaymentStatus.PAID_REVIEW,
            PaymentStatus.REFUNDED,
        }:
            await add_case_history_event(
                self.db,
                actor_type="payment_provider",
                actor_id=None,
                case_id=case.id,
                action="PAYMENT_FAILURE_IGNORED",
                old_value={"status": payment.status},
                new_value={
                    "payment_id": payment.id,
                    "payload": provider_payload or {},
                },
                comment="Позднее уведомление об ошибке не изменило полученный платёж",
            )
            await self.db.flush()
            return payment

        if payment.status in {
            PaymentStatus.FAILED,
            PaymentStatus.CANCELLED,
            PaymentStatus.EXPIRED,
        }:
            return payment

        old = payment.status
        payment.status = PaymentStatus.FAILED
        await add_case_history_event(
            self.db,
            actor_type="payment_provider",
            actor_id=None,
            case_id=case.id,
            action="PAYMENT_FAILED",
            old_value={"status": old},
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "payload": provider_payload or {},
            },
        )
        await self.db.flush()
        return payment
