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


PROTECTED_RECEIVED_PAYMENT_STATUSES = {
    PaymentStatus.PAID_REVIEW,
    PaymentStatus.REFUND_PENDING,
    PaymentStatus.REFUND_DECLINED,
    PaymentStatus.REFUNDED,
}
INACTIVE_M2_PAYMENT_STATUSES = {
    PaymentStatus.EXPIRED,
    PaymentStatus.CANCELLED,
    PaymentStatus.FAILED,
}
M1_EXPECTED_PAYMENT_CASE_STATUSES = {
    PaymentCode.M1_INITIAL_PAYMENT: CaseStatus.M1_WAITING_PAYMENT_30000,
    PaymentCode.M1_COURT_PAYMENT: CaseStatus.M1_WAITING_PAYMENT_70000,
    PaymentCode.M1_SUCCESS_FEE: CaseStatus.M1_WAITING_SUCCESS_FEE,
}


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
        actor_type: str = "payment_provider",
        actor_id: int | None = None,
    ) -> Payment:
        old_status = payment.status
        payment.status = PaymentStatus.PAID_REVIEW
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
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

    async def _mark_stale_m1_payment_refund(
        self,
        *,
        payment: Payment,
        case,
        expected_status: CaseStatus,
        provider_payload: dict | None,
        actor_type: str,
        actor_id: int | None,
    ) -> Payment:
        """Preserve money truth without resurrecting an obsolete M1 stage."""

        old_status = payment.status
        payment.status = PaymentStatus.REFUND_PENDING
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="M1_STALE_PAYMENT_REFUND_REQUIRED",
            old_value={
                "payment_id": payment.id,
                "payment_status": old_status,
                "case_status": str(case.status),
                "case_route": case.route,
            },
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "payment_status": payment.status,
                "expected_case_status": expected_status.value,
                "case_status_preserved": str(case.status),
                "case_route_preserved": case.route,
                "payload": provider_payload or {},
            },
            comment=(
                "Деньги поступили по M1-ссылке, которая больше не соответствует текущему "
                "этапу дела. Дело не изменено; платёж направлен на ручной фактический возврат."
            ),
        )
        await self.notifications.emit(
            event_code="M1_STALE_PAYMENT_REFUND_PENDING",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "amount": str(payment.amount),
                "payment_title": payment.title,
            },
            dedupe_key=f"payment:{payment.id}:m1-stale-refund-pending",
        )
        await self.db.flush()
        return payment

    async def process_successful_payment(
        self,
        *,
        payment,
        case,
        provider_payload=None,
        actor_type: str = "payment_provider",
        actor_id: int | None = None,
        processed_action: str = "PAYMENT_WEBHOOK_PROCESSED",
    ):
        """Apply a verified successful payment through the canonical state machine.

        Provider webhooks use the defaults. A controlled offline confirmation may
        provide an admin actor and a distinct audit action while still using the
        exact same payment/case transition logic.
        """

        payment = await self._lock_payment(payment.id)

        if payment.status in PROTECTED_RECEIVED_PAYMENT_STATUSES:
            return payment

        if payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
            if payment.status in INACTIVE_M2_PAYMENT_STATUSES:
                return await self._mark_consultation_payment_review(
                    payment=payment,
                    case=case,
                    reason=(
                        "Деньги поступили по уже закрытой, отменённой или ранее "
                        "неуспешной ссылке. Автоматическое изменение консультации запрещено."
                    ),
                    provider_payload=provider_payload,
                    actor_type=actor_type,
                    actor_id=actor_id,
                )

            consultation_service = ConsultationService(self.db)
            consultation = await consultation_service.get_current_for_case(case.id)

            if not consultation or not consultation.slot_id:
                return await self._mark_consultation_payment_review(
                    payment=payment,
                    case=case,
                    reason="Активная консультация или связанный слот не найдены",
                    provider_payload=provider_payload,
                    actor_type=actor_type,
                    actor_id=actor_id,
                )

            if payment.status == PaymentStatus.PAID:
                slot = await consultation_service.slots.get_slot(
                    consultation.slot_id
                )
                if (
                    consultation.status == ConsultationStatus.BOOKED
                    and slot
                    and slot.status == "booked"
                    and slot.consultation_id == consultation.id
                ):
                    return payment
                return await self._mark_consultation_payment_review(
                    payment=payment,
                    case=case,
                    reason=(
                        "Платёж уже отмечен оплаченным, но действующая "
                        "подтверждённая консультация не найдена"
                    ),
                    provider_payload=provider_payload,
                    actor_type=actor_type,
                    actor_id=actor_id,
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
                    actor_type=actor_type,
                    actor_id=actor_id,
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
                    actor_type=actor_type,
                    actor_id=actor_id,
                )

            await self.payments.mark_paid(
                payment=payment,
                case=case,
                actor_type=actor_type,
                actor_id=actor_id,
            )
            if case.status != CaseStatus.M2_CONSULTATION_BOOKED:
                await self.cases.change_status(
                    case=case,
                    next_status=CaseStatus.M2_CONSULTATION_BOOKED,
                    actor_type="system",
                    actor_id=None,
                    comment="Консультация подтверждена после оплаты",
                )
            await self.notifications.emit(
                event_code="M2_CONSULTATION_BOOKED",
                case_id=case.id,
                user_id=case.client_id,
                payload={
                    "case_number": case.case_number,
                    "date": (
                        consultation.scheduled_at.strftime("%d.%m.%Y %H:%M")
                        if consultation.scheduled_at
                        else "уточняется"
                    ),
                },
                dedupe_key=f"{expected_reservation_key}:booked",
            )
        else:
            if payment.status == PaymentStatus.PAID:
                return payment

            expected_status = M1_EXPECTED_PAYMENT_CASE_STATUSES.get(payment.payment_code)
            if expected_status is not None and (
                str(case.route or "") != "M1"
                or str(case.status) != expected_status.value
            ):
                return await self._mark_stale_m1_payment_refund(
                    payment=payment,
                    case=case,
                    expected_status=expected_status,
                    provider_payload=provider_payload,
                    actor_type=actor_type,
                    actor_id=actor_id,
                )

            await self.payments.mark_paid(
                payment=payment,
                case=case,
                actor_type=actor_type,
                actor_id=actor_id,
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
                    comment=f"Автопереход после оплаты {payment.payment_code}",
                )
            if (
                payment.payment_code == PaymentCode.M1_SUCCESS_FEE
                and case.status == CaseStatus.M1_CLOSED
            ):
                await self.notifications.emit(
                    event_code="M1_CLOSED",
                    case_id=case.id,
                    payload={"case_number": case.case_number},
                    dedupe_key=f"payment:{payment.id}:m1-closed",
                )

        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action=processed_action,
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
            *PROTECTED_RECEIVED_PAYMENT_STATUSES,
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