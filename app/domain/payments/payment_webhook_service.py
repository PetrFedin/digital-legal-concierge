from datetime import datetime, timezone

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.consultations.payment_lifecycle_service import (
    ConsultationPaymentLifecycleError,
    ConsultationPaymentLifecycleService,
)
from app.domain.payments.payment_processing_outcomes import PaymentProcessingOutcome
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus


class PaymentWebhookService:
    def __init__(self, db):
        self.db = db
        self.payments = PaymentService(db)
        self.cases = CaseService(db)

    @staticmethod
    def _validate_payment_case(*, payment, case) -> None:
        if payment is None or case is None or payment.case_id != case.id:
            raise ValueError("Платёж не принадлежит переданному делу.")

    @staticmethod
    def _mark_processing_result(
        payment,
        *,
        outcome: PaymentProcessingOutcome,
        error: str | None = None,
    ) -> None:
        payment.processing_outcome = outcome
        payment.manual_review_required = outcome in {
            PaymentProcessingOutcome.MANUAL_REVIEW_REQUIRED,
            PaymentProcessingOutcome.CONFLICT,
        }
        payment.processing_error = error
        payment.processed_at = (
            datetime.now(timezone.utc)
            if outcome == PaymentProcessingOutcome.PROCESSED
            else None
        )

    async def _mark_manual_review(
        self,
        *,
        payment,
        case,
        outcome: PaymentProcessingOutcome,
        error: str,
        provider_payload=None,
    ):
        self._mark_processing_result(
            payment,
            outcome=outcome,
            error=error,
        )
        await add_case_history_event(
            self.db,
            actor_type="payment_provider",
            actor_id=None,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_REQUIRES_MANUAL_REVIEW",
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "processing_outcome": outcome.value,
                "error": error,
                "payload": provider_payload or {},
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
        self._validate_payment_case(payment=payment, case=case)

        # Provider retries must not repeat domain transitions or history events.
        if payment.status == PaymentStatus.PAID and payment.processing_outcome is not None:
            return payment

        if payment.status != PaymentStatus.PAID:
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

        consultation = None
        if payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
            try:
                consultation = await ConsultationPaymentLifecycleService(
                    self.db
                ).mark_paid_pending_confirmation(
                    case=case,
                    source="payment_webhook",
                )
            except ConsultationPaymentLifecycleError as exc:
                return await self._mark_manual_review(
                    payment=payment,
                    case=case,
                    outcome=PaymentProcessingOutcome.CONFLICT,
                    error=str(exc),
                    provider_payload=provider_payload,
                )
        elif payment.payment_code in mapping:
            for status in mapping[payment.payment_code]:
                await self.cases.change_status(
                    case=case,
                    next_status=status,
                    actor_type="system",
                    actor_id=None,
                    force=True,
                    comment=f"Автопереход после оплаты {payment.payment_code}",
                )
        else:
            return await self._mark_manual_review(
                payment=payment,
                case=case,
                outcome=PaymentProcessingOutcome.MANUAL_REVIEW_REQUIRED,
                error="Для кода платежа не настроена доменная обработка.",
                provider_payload=provider_payload,
            )

        self._mark_processing_result(
            payment,
            outcome=PaymentProcessingOutcome.PROCESSED,
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
                "processing_outcome": payment.processing_outcome.value,
                "consultation_id": consultation.id if consultation else None,
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
        self._validate_payment_case(payment=payment, case=case)

        # A delayed failure/cancellation callback must never overwrite success.
        if payment.status == PaymentStatus.PAID:
            return payment
        if payment.status == PaymentStatus.FAILED:
            return payment

        old = payment.status
        payment.status = PaymentStatus.FAILED
        payment.processing_outcome = None
        payment.processed_at = None
        payment.manual_review_required = False
        payment.processing_error = None
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
