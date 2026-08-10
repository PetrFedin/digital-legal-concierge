from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_review_service import PaymentReviewService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.payment import Payment


class OrphanPaymentReviewResolutionError(ValueError):
    pass


class OrphanPaymentReviewService:
    """Resolve a PAID_REVIEW whose encoded consultation no longer exists.

    This path is intentionally refund-only. It never changes the case,
    consultation, slot or next action. Its only business mutation is moving the
    exact received payment into REFUND_PENDING after an administrator records a
    reconciliation comment.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)

    @staticmethod
    def _require_comment(comment: str | None) -> str:
        normalized = str(comment or "").strip()
        if len(normalized) < 5:
            raise OrphanPaymentReviewResolutionError(
                "Укажите результат сверки не короче 5 символов"
            )
        return normalized

    async def route_to_refund(
        self,
        *,
        payment_id: int,
        actor_id: int | None,
        comment: str,
    ) -> Payment:
        comment = self._require_comment(comment)
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
            raise OrphanPaymentReviewResolutionError(
                "Refund-only сверка доступна только для платежа консультации"
            )
        if payment.status == PaymentStatus.REFUND_PENDING:
            return payment
        if payment.status != PaymentStatus.PAID_REVIEW:
            raise OrphanPaymentReviewResolutionError(
                "Платёж не находится в статусе проверки"
            )

        linked_consultation_id, linked_slot_id = PaymentReviewService.reservation_context(
            payment
        )
        if linked_consultation_id is None:
            raise OrphanPaymentReviewResolutionError(
                "У платежа нет точной старой привязки. Выберите консультацию в центре сверки."
            )

        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == payment.case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise LookupError("Дело не найдено")

        existing = (
            await self.db.execute(
                select(Consultation.id).where(
                    Consultation.id == linked_consultation_id,
                    Consultation.case_id == case.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise OrphanPaymentReviewResolutionError(
                "Связанная консультация существует. Используйте обычное решение сверки, "
                "чтобы не обойти проверку брони."
            )

        old_status = payment.status
        payment.status = PaymentStatus.REFUND_PENDING
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="CONSULTATION_PAYMENT_REVIEW_RESOLVED",
            old_value={
                "payment_id": payment.id,
                "payment_status": old_status,
                "reservation_key": payment.reservation_key,
                "case_status": case.status,
                "case_next_action": case.next_action,
            },
            new_value={
                "payment_id": payment.id,
                "payment_status": payment.status,
                "consultation_id": None,
                "orphan_consultation_id": linked_consultation_id,
                "orphan_slot_id": linked_slot_id,
                "decision": "refund_orphan",
                "case_status": case.status,
                "case_next_action": case.next_action,
                "case_context_preserved": True,
            },
            comment=comment,
        )
        await self.notifications.emit(
            event_code="CONSULTATION_PAYMENT_REVIEW_REFUND_PENDING",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "amount": str(payment.amount),
                "booking_preserved": True,
                "case_context_preserved": True,
            },
            dedupe_key=f"payment-review:{payment.id}:orphan-refund-pending",
        )
        await self.db.flush()
        return payment
