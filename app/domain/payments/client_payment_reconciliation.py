from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.consultations.consultation_service import TERMINAL_CONSULTATION_STATUSES
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.payment import Payment


ACTIVE_LINK_STATUSES = {
    PaymentStatus.PENDING,
    PaymentStatus.WAITING_CONFIRMATION,
}


class ClientPaymentReconciliationService:
    """Fail closed before an M2 payment URL is shown to a client.

    The provider webhook and hold-expiry scheduler remain authoritative for money
    and booking writes. This service closes the UI race between those background
    paths and a client opening an old Telegram payment card. It only expires an
    unpaid link after locking the exact payment/case/consultation/slot context.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _reservation_context(payment: Payment) -> tuple[int | None, int | None]:
        parts = str(payment.reservation_key or "").split(":")
        if len(parts) != 4 or parts[0] != "consultation" or parts[2] != "slot":
            return None, None
        try:
            consultation_id = int(parts[1])
            slot_id = int(parts[3])
        except (TypeError, ValueError):
            return None, None
        if consultation_id <= 0 or slot_id <= 0:
            return None, None
        return consultation_id, slot_id

    async def _lock_payment(self, payment_id: int) -> Payment:
        payment = (
            await self.db.execute(
                select(Payment)
                .where(Payment.id == int(payment_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if payment is None:
            raise LookupError("Платёж не найден")
        return payment

    async def _lock_case(self, case_id: int) -> Case:
        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == int(case_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело не найдено")
        return case

    async def _lock_current_consultation(self, case_id: int) -> Consultation | None:
        return (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.case_id == int(case_id))
                .where(
                    Consultation.status.notin_(
                        [status.value for status in TERMINAL_CONSULTATION_STATUSES]
                    )
                )
                .order_by(Consultation.created_at.desc(), Consultation.id.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalars().first()

    async def _expire(
        self,
        *,
        payment: Payment,
        case: Case,
        reason: str,
        consultation: Consultation | None,
        slot: ConsultationSlot | None,
        restore_slot_selection: bool,
    ) -> None:
        if payment.status not in ACTIVE_LINK_STATUSES:
            return

        old_status = payment.status
        payment.status = PaymentStatus.EXPIRED

        if restore_slot_selection:
            if (
                slot is not None
                and consultation is not None
                and slot.consultation_id == consultation.id
                and slot.status == "held"
            ):
                slot.status = "available"
                slot.hold_expires_at = None
                slot.held_by_user_id = None
                slot.consultation_id = None
            if consultation is not None:
                consultation.slot_id = None
                consultation.lawyer_id = None
                consultation.scheduled_at = None
                consultation.status = ConsultationStatus.SLOT_PENDING
            if str(case.status) == CaseStatus.M2_PAYMENT_PENDING.value:
                await self.cases.change_status(
                    case=case,
                    next_status=CaseStatus.M2_SLOT_PENDING,
                    actor_type="system",
                    actor_id=None,
                    comment=(
                        "Перед показом платёжной ссылки обнаружен недействительный "
                        "резерв консультации. Клиент возвращён к выбору времени."
                    ),
                )

        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=case.id,
            action="CONSULTATION_PAYMENT_LINK_EXPIRED",
            old_value={
                "payment_id": int(payment.id),
                "status": str(old_status),
                "reservation_key": payment.reservation_key,
            },
            new_value={
                "payment_id": int(payment.id),
                "status": str(payment.status),
                "reason": reason,
                "case_status": str(case.status),
                "consultation_id": int(consultation.id) if consultation else None,
                "slot_id": int(slot.id) if slot else None,
            },
            comment=(
                "Старая M2-ссылка скрыта до перехода к провайдеру: её резерв "
                "больше не совпадает с актуальной записью клиента."
            ),
        )
        await self.db.flush()

    async def reconcile(
        self,
        *,
        payment_id: int,
        case_id: int,
    ) -> tuple[Payment, Case, bool]:
        # Lock order intentionally matches the payment webhook: Payment -> Case.
        payment = await self._lock_payment(payment_id)
        if int(payment.case_id) != int(case_id):
            raise ValueError("Платёж не относится к указанному делу")
        case = await self._lock_case(case_id)

        if (
            payment.payment_code != PaymentCode.M2_CONSULTATION_PAYMENT
            or payment.status not in ACTIVE_LINK_STATUSES
        ):
            return payment, case, False

        current = await self._lock_current_consultation(case.id)
        linked_consultation_id, linked_slot_id = self._reservation_context(payment)
        reason: str | None = None
        restore_slot_selection = False
        slot: ConsultationSlot | None = None

        if (
            str(case.route or "") != RouteCode.M2.value
            or str(case.status) != CaseStatus.M2_PAYMENT_PENDING.value
        ):
            reason = "case_not_waiting_for_this_m2_payment"
        elif current is None:
            reason = "active_consultation_missing"
            restore_slot_selection = True
        elif linked_consultation_id is None or linked_slot_id is None:
            reason = "payment_reservation_key_missing_or_invalid"
        elif int(current.id) != int(linked_consultation_id):
            reason = "payment_points_to_previous_consultation"
        elif int(current.slot_id or 0) != int(linked_slot_id):
            reason = "payment_points_to_previous_slot"
        elif current.status != ConsultationStatus.PAYMENT_PENDING:
            reason = "consultation_not_waiting_for_payment"
        else:
            slot = (
                await self.db.execute(
                    select(ConsultationSlot)
                    .where(ConsultationSlot.id == int(linked_slot_id))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if slot is None:
                reason = "reserved_slot_missing"
                restore_slot_selection = True
            elif (
                slot.status != "held"
                or int(slot.consultation_id or 0) != int(current.id)
                or slot.hold_expires_at is None
                or self._as_utc(slot.hold_expires_at) < now
                or self._as_utc(slot.starts_at) <= now
            ):
                reason = "slot_hold_expired_or_broken"
                restore_slot_selection = True
            else:
                expected_key = PaymentService.consultation_reservation_key(
                    current.id,
                    slot.id,
                )
                if payment.reservation_key != expected_key:
                    reason = "reservation_key_mismatch"

        if reason is None:
            return payment, case, False

        await self._expire(
            payment=payment,
            case=case,
            reason=reason,
            consultation=current,
            slot=slot,
            restore_slot_selection=restore_slot_selection,
        )
        return payment, case, True


__all__ = [
    "ACTIVE_LINK_STATUSES",
    "ClientPaymentReconciliationService",
]
