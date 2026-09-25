from datetime import datetime

from sqlalchemy import select

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.cases.self_filing_business_calendar import BusinessCalendarError
from app.domain.cases.self_filing_email_sender import SelfFilingEmailConfigurationError
from app.domain.cases.self_filing_service import SelfFilingError, SelfFilingService
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_lifecycle import PaymentLifecycleService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.payment import Payment
from app.presentation_time import format_business_datetime


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
    PaymentCode.M1_SELF_FILING_PACKAGE: CaseStatus.M1_SELF_FILING_PAYMENT_PENDING,
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
                .execution_options(populate_existing=True)
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
        occurred_at: datetime | None = None,
    ) -> Payment:
        transition = PaymentLifecycleService.transition(
            payment,
            to_status=PaymentStatus.PAID_REVIEW,
            occurred_at=occurred_at,
        )
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CONSULTATION_PAYMENT_REVIEW_REQUIRED",
            old_value={"status": transition.old_status.value},
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "status": transition.new_status.value,
                "money_received_at": (
                    payment.paid_at.isoformat() if payment.paid_at else None
                ),
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

    async def _mark_self_filing_payment_review(
        self,
        *,
        payment: Payment,
        case,
        reason: str,
        provider_payload: dict | None,
        actor_type: str,
        actor_id: int | None,
        occurred_at: datetime | None = None,
    ) -> Payment:
        """Preserve received-money truth when package activation cannot complete.

        The provider-confirmed money fact must survive even if the legal/package
        side cannot start its SLA atomically (for example because the controlled
        business calendar no longer covers the due date). The Case deliberately
        remains on PAYMENT_PENDING until an administrator reconciles the exact
        received payment through the self-filing product.
        """

        transition = PaymentLifecycleService.transition(
            payment,
            to_status=PaymentStatus.PAID_REVIEW,
            occurred_at=payment.paid_at or occurred_at,
        )
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="SELF_FILING_PAYMENT_REVIEW_REQUIRED",
            old_value={
                "payment_id": payment.id,
                "payment_status": transition.old_status.value,
                "case_status": str(case.status),
            },
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "payment_status": transition.new_status.value,
                "money_received_at": (
                    payment.paid_at.isoformat() if payment.paid_at else None
                ),
                "case_status_preserved": str(case.status),
                "reason": reason,
                "payload": provider_payload or {},
            },
            comment=(
                "Деньги получены, но автоматический запуск подготовки пакета и "
                "двухдневного SLA остановлен безопасностью. Повторная оплата "
                "заблокирована; требуется финансовая сверка администратора."
            ),
        )
        await self.notifications.emit(
            event_code="SELF_FILING_PAYMENT_REVIEW_REQUIRED",
            case_id=case.id,
            user_id=case.client_id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "amount": str(payment.amount),
                "reason": reason,
            },
            dedupe_key=f"payment:{payment.id}:self-filing-review",
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
        occurred_at: datetime | None = None,
    ) -> Payment:
        """Preserve money truth without resurrecting an obsolete M1 stage."""

        transition = PaymentLifecycleService.transition(
            payment,
            to_status=PaymentStatus.REFUND_PENDING,
            occurred_at=occurred_at,
        )
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="M1_STALE_PAYMENT_REFUND_REQUIRED",
            old_value={
                "payment_id": payment.id,
                "payment_status": transition.old_status.value,
                "case_status": str(case.status),
                "case_route": case.route,
            },
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "payment_status": transition.new_status.value,
                "money_received_at": (
                    payment.paid_at.isoformat() if payment.paid_at else None
                ),
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

    async def _emit_m1_paid_next_step(self, *, payment: Payment, case) -> None:
        if (
            payment.payment_code == PaymentCode.M1_INITIAL_PAYMENT
            and str(case.status) == CaseStatus.M1_POWER_OF_ATTORNEY.value
        ):
            await self.notifications.emit(
                event_code="M1_INITIAL_PAYMENT_CONFIRMED",
                case_id=case.id,
                user_id=case.client_id,
                payload={"case_number": case.case_number},
                dedupe_key=f"payment:{payment.id}:m1-initial-confirmed",
            )
            return
        if (
            payment.payment_code == PaymentCode.M1_COURT_PAYMENT
            and str(case.status) == CaseStatus.M1_ENFORCEMENT.value
        ):
            await self.notifications.emit(
                event_code="M1_COURT_PAYMENT_CONFIRMED",
                case_id=case.id,
                user_id=case.client_id,
                payload={"case_number": case.case_number},
                dedupe_key=f"payment:{payment.id}:m1-court-confirmed",
            )

    async def process_successful_payment(
        self,
        *,
        payment,
        case,
        provider_payload=None,
        actor_type: str = "payment_provider",
        actor_id: int | None = None,
        processed_action: str = "PAYMENT_WEBHOOK_PROCESSED",
        occurred_at: datetime | None = None,
    ):
        """Apply a verified successful payment through the canonical state machine.

        ``occurred_at`` is the provider business timestamp when the provider can
        prove it (for YooKassa this is ``captured_at``). If unavailable, the
        lifecycle service deliberately falls back to our UTC processing time.
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
                    occurred_at=occurred_at,
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
                    occurred_at=occurred_at,
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
                    occurred_at=occurred_at,
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
                    occurred_at=occurred_at,
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
                    occurred_at=occurred_at,
                )

            await self.payments.mark_paid(
                payment=payment,
                case=case,
                actor_type=actor_type,
                actor_id=actor_id,
                occurred_at=occurred_at,
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
                    "date": format_business_datetime(
                        consultation.scheduled_at,
                        empty="уточняется",
                    ),
                },
                dedupe_key=f"{expected_reservation_key}:booked",
            )
        else:
            if payment.status == PaymentStatus.PAID:
                return payment

            expected_status = M1_EXPECTED_PAYMENT_CASE_STATUSES.get(payment.payment_code)
            self_filing_mode_mismatch = bool(
                payment.payment_code == PaymentCode.M1_SELF_FILING_PACKAGE
                and str(getattr(case, "service_mode", "") or "")
                != M1ServiceMode.SELF_FILING_PACKAGE.value
            )
            if expected_status is not None and (
                str(case.route or "") != "M1"
                or str(case.status) != expected_status.value
                or self_filing_mode_mismatch
            ):
                return await self._mark_stale_m1_payment_refund(
                    payment=payment,
                    case=case,
                    expected_status=expected_status,
                    provider_payload=provider_payload,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    occurred_at=occurred_at,
                )

            await self.payments.mark_paid(
                payment=payment,
                case=case,
                actor_type=actor_type,
                actor_id=actor_id,
                occurred_at=occurred_at,
            )
            if payment.payment_code == PaymentCode.M1_SELF_FILING_PACKAGE:
                # The provider-confirmed money fact is outside the savepoint.
                # Package/SLA activation is inside it. If a legal/operational
                # precondition changed after link creation, only package state
                # rolls back; received money becomes PAID_REVIEW and remains
                # visible for controlled reconciliation.
                try:
                    async with self.db.begin_nested():
                        package = await SelfFilingService(
                            self.db
                        ).start_preparation_after_payment(
                            case=case,
                            payment=payment,
                            actor_type=actor_type,
                            actor_id=actor_id,
                            occurred_at=occurred_at,
                        )
                except (
                    SelfFilingError,
                    BusinessCalendarError,
                    SelfFilingEmailConfigurationError,
                    KeyError,
                    ValueError,
                ) as error:
                    await self._mark_self_filing_payment_review(
                        payment=payment,
                        case=case,
                        reason=str(error),
                        provider_payload=provider_payload,
                        actor_type=actor_type,
                        actor_id=actor_id,
                        occurred_at=occurred_at,
                    )
                else:
                    await self.notifications.emit(
                        event_code="SELF_FILING_PAYMENT_CONFIRMED",
                        case_id=case.id,
                        user_id=case.client_id,
                        payload={
                            "case_number": case.case_number,
                            "payment_id": payment.id,
                            "sla_due_at": (
                                package.sla_due_at.isoformat()
                                if package.sla_due_at
                                else None
                            ),
                        },
                        dedupe_key=f"payment:{payment.id}:self-filing-confirmed",
                    )
            else:
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
                if payment.payment_code == PaymentCode.M1_SUCCESS_FEE:
                    case.close_reason = "M1_SUCCESS_FEE_PAID"
                for status in mapping.get(payment.payment_code, []):
                    await self.cases.change_status(
                        case=case,
                        next_status=status,
                        actor_type="system",
                        actor_id=None,
                        comment=f"Автопереход после оплаты {payment.payment_code}",
                    )
                await self._emit_m1_paid_next_step(payment=payment, case=case)
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
                "money_received_at": (
                    payment.paid_at.isoformat() if payment.paid_at else None
                ),
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

        transition = PaymentLifecycleService.transition(
            payment,
            to_status=PaymentStatus.FAILED,
        )
        await add_case_history_event(
            self.db,
            actor_type="payment_provider",
            actor_id=None,
            case_id=case.id,
            action="PAYMENT_FAILED",
            old_value={"status": transition.old_status.value},
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "payload": provider_payload or {},
            },
        )
        await self.notifications.emit(
            event_code="PAYMENT_FAILED_CLIENT",
            case_id=case.id,
            user_id=case.client_id,
            payload={
                "case_number": case.case_number,
                "payment_title": payment.title,
            },
            dedupe_key=f"payment:{payment.id}:failed-client",
        )
        await self.db.flush()
        return payment
