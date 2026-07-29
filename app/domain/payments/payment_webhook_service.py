from datetime import datetime, timezone

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.payment_lifecycle_service import (
    ConsultationPaymentLifecycleError,
    ConsultationPaymentLifecycleService,
)
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.m1_payment_lifecycle_service import (
    M1PaymentLifecycleError,
    M1PaymentLifecycleService,
)
from app.domain.payments.payment_processing_outcomes import PaymentProcessingOutcome
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus


class PaymentWebhookService:
    def __init__(self, db):
        self.db = db
        self.payments = PaymentService(db)

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
        actor_type: str = "payment_provider",
        actor_id: int | None = None,
    ):
        already_recorded = (
            payment.processing_outcome == outcome
            and payment.processing_error == error
            and payment.manual_review_required is True
        )
        self._mark_processing_result(
            payment,
            outcome=outcome,
            error=error,
        )
        if already_recorded:
            await self.db.flush()
            return payment

        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
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
        allow_reprocess: bool = False,
        actor_type: str = "payment_provider",
        actor_id: int | None = None,
        source: str = "payment_webhook",
    ):
        self._validate_payment_case(payment=payment, case=case)

        previous_outcome = payment.processing_outcome

        # Provider retries must not repeat completed domain transitions or history events.
        if (
            payment.status == PaymentStatus.PAID
            and payment.processing_outcome == PaymentProcessingOutcome.PROCESSED
        ):
            return payment

        # A conflict or manual-review result is stable for provider retries, but an
        # authenticated administrator may retry after correcting the underlying data.
        if (
            payment.status == PaymentStatus.PAID
            and payment.processing_outcome is not None
            and not allow_reprocess
        ):
            return payment

        if payment.status != PaymentStatus.PAID:
            await self.payments.mark_paid(
                payment=payment,
                case=case,
                actor_type=actor_type,
                actor_id=actor_id,
            )

        consultation = None
        m1_transitions: list[str] = []

        if payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
            try:
                consultation = await ConsultationPaymentLifecycleService(
                    self.db
                ).mark_paid_pending_confirmation(
                    case=case,
                    source=source,
                )
            except ConsultationPaymentLifecycleError as exc:
                return await self._mark_manual_review(
                    payment=payment,
                    case=case,
                    outcome=PaymentProcessingOutcome.CONFLICT,
                    error=str(exc),
                    provider_payload=provider_payload,
                    actor_type=actor_type,
                    actor_id=actor_id,
                )
        elif payment.payment_code in {
            PaymentCode.M1_INITIAL_PAYMENT,
            PaymentCode.M1_COURT_PAYMENT,
            PaymentCode.M1_SUCCESS_FEE,
        }:
            try:
                m1_transitions = await M1PaymentLifecycleService(
                    self.db
                ).apply_successful_payment(
                    case=case,
                    payment_code=payment.payment_code,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    source=source,
                )
            except M1PaymentLifecycleError as exc:
                return await self._mark_manual_review(
                    payment=payment,
                    case=case,
                    outcome=PaymentProcessingOutcome.CONFLICT,
                    error=str(exc),
                    provider_payload=provider_payload,
                    actor_type=actor_type,
                    actor_id=actor_id,
                )
        else:
            return await self._mark_manual_review(
                payment=payment,
                case=case,
                outcome=PaymentProcessingOutcome.MANUAL_REVIEW_REQUIRED,
                error="Для кода платежа не настроена доменная обработка.",
                provider_payload=provider_payload,
                actor_type=actor_type,
                actor_id=actor_id,
            )

        self._mark_processing_result(
            payment,
            outcome=PaymentProcessingOutcome.PROCESSED,
        )
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_PROCESSED",
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "processing_outcome": payment.processing_outcome.value,
                "previous_processing_outcome": (
                    previous_outcome.value if previous_outcome is not None else None
                ),
                "manual_reprocess": allow_reprocess,
                "consultation_id": consultation.id if consultation else None,
                "m1_transitions": m1_transitions,
                "source": source,
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

        already_failed = payment.status == PaymentStatus.FAILED
        old_status = payment.status
        payment.status = PaymentStatus.FAILED.value
        payment.payment_url = None
        payment.processing_outcome = None
        payment.processed_at = None
        payment.manual_review_required = False
        payment.processing_error = None

        if payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
            case.next_action = "Повторите оплату консультации или выберите другое время"

        if not already_failed:
            await add_case_history_event(
                self.db,
                actor_type="payment_provider",
                actor_id=None,
                case_id=case.id,
                action="PAYMENT_FAILED",
                old_value={"status": old_status},
                new_value={
                    "payment_id": payment.id,
                    "payment_code": payment.payment_code,
                    "payload": provider_payload or {},
                },
            )

        await NotificationEngine(self.db).emit(
            event_code="PAYMENT_FAILED",
            case_id=case.id,
            user_id=case.client_id,
            payload={"case_number": case.case_number},
        )
        await self.db.flush()
        return payment
