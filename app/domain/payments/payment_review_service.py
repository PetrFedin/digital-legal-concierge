from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.payment import Payment


class PaymentReviewResolutionError(ValueError):
    pass


class PaymentReviewService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.slots = SlotService(db)
        self.cases = CaseService(db)
        self.notifications = NotificationEngine(db)

    async def _lock_payment(self, payment_id: int) -> Payment:
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
            raise PaymentReviewResolutionError(
                "Платёж не относится к консультации"
            )
        return payment

    async def _lock_case(self, case_id: int) -> Case:
        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise LookupError("Дело не найдено")
        return case

    async def _lock_latest_consultation(self, case_id: int) -> Consultation:
        consultation = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.case_id == case_id)
                .order_by(Consultation.created_at.desc(), Consultation.id.desc())
                .with_for_update()
            )
        ).scalars().first()
        if not consultation:
            raise PaymentReviewResolutionError(
                "По делу не найдена консультация для оплаченного платежа"
            )
        return consultation

    @staticmethod
    def _require_comment(comment: str | None) -> str:
        normalized = str(comment or "").strip()
        if len(normalized) < 5:
            raise PaymentReviewResolutionError(
                "Укажите комментарий администратора не короче 5 символов"
            )
        return normalized

    async def _advance_case_after_received_payment(
        self,
        *,
        case: Case,
        actor_id: int | None,
        comment: str,
    ) -> None:
        if case.status == CaseStatus.M2_SLOT_PENDING:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_PAYMENT_PENDING,
                actor_type="admin",
                actor_id=actor_id,
                comment="Оплаченный слот подтверждён при ручной проверке платежа",
            )
        if case.status != CaseStatus.M2_CONSULTATION_BOOKED:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_CONSULTATION_BOOKED,
                actor_type="admin",
                actor_id=actor_id,
                comment=comment,
            )

    async def _record_resolution(
        self,
        *,
        payment: Payment,
        case: Case,
        consultation: Consultation,
        actor_id: int | None,
        decision: str,
        comment: str,
        old_value: dict,
        new_value: dict,
    ) -> None:
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="CONSULTATION_PAYMENT_REVIEW_RESOLVED",
            old_value=old_value,
            new_value={
                "payment_id": payment.id,
                "consultation_id": consultation.id,
                "decision": decision,
                **new_value,
            },
            comment=comment,
        )

    async def confirm_existing_booking(
        self,
        *,
        payment_id: int,
        actor_id: int | None,
        comment: str,
    ) -> tuple[Payment, Consultation]:
        comment = self._require_comment(comment)
        payment = await self._lock_payment(payment_id)
        case = await self._lock_case(payment.case_id)
        consultation = await self._lock_latest_consultation(case.id)

        if payment.status == PaymentStatus.PAID:
            return payment, consultation
        if payment.status != PaymentStatus.PAID_REVIEW:
            raise PaymentReviewResolutionError(
                "Платёж не находится в статусе PAID_REVIEW"
            )
        if consultation.status != ConsultationStatus.BOOKED:
            raise PaymentReviewResolutionError(
                "Консультация не находится в статусе BOOKED"
            )
        if not consultation.slot_id:
            raise PaymentReviewResolutionError(
                "У консультации отсутствует связанный слот"
            )

        slot = await self.slots.get_slot_for_update(consultation.slot_id)
        if (
            not slot
            or slot.status != "booked"
            or slot.consultation_id != consultation.id
        ):
            raise PaymentReviewResolutionError(
                "Существующая бронь не подтверждена целостной связью со слотом"
            )

        old_value = {
            "payment_status": payment.status,
            "reservation_key": payment.reservation_key,
        }
        payment.status = PaymentStatus.PAID
        payment.reservation_key = PaymentService.consultation_reservation_key(
            consultation.id,
            slot.id,
        )
        await self._advance_case_after_received_payment(
            case=case,
            actor_id=actor_id,
            comment="Подтверждение существующей брони после проверки платежа",
        )

        await self._record_resolution(
            payment=payment,
            case=case,
            consultation=consultation,
            actor_id=actor_id,
            decision="confirm_existing",
            comment=comment,
            old_value=old_value,
            new_value={
                "payment_status": payment.status,
                "slot_id": slot.id,
                "scheduled_at": slot.starts_at.isoformat(),
            },
        )
        await self.notifications.emit(
            event_code="CONSULTATION_PAYMENT_REVIEW_RESOLVED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "date": slot.starts_at.strftime("%d.%m.%Y %H:%M"),
            },
            dedupe_key=f"payment-review:{payment.id}:confirmed",
        )
        await self.db.flush()
        return payment, consultation

    async def assign_new_slot(
        self,
        *,
        payment_id: int,
        slot_id: int,
        actor_id: int | None,
        comment: str,
    ) -> tuple[Payment, Consultation]:
        comment = self._require_comment(comment)
        payment = await self._lock_payment(payment_id)
        case = await self._lock_case(payment.case_id)
        consultation = await self._lock_latest_consultation(case.id)

        if payment.status == PaymentStatus.PAID:
            return payment, consultation
        if payment.status != PaymentStatus.PAID_REVIEW:
            raise PaymentReviewResolutionError(
                "Платёж не находится в статусе PAID_REVIEW"
            )
        if consultation.status in {
            ConsultationStatus.DONE,
            ConsultationStatus.CANCELLED,
            ConsultationStatus.CLOSED,
        }:
            raise PaymentReviewResolutionError(
                "Закрытую консультацию нельзя автоматически восстановить"
            )

        old_value = {
            "payment_status": payment.status,
            "reservation_key": payment.reservation_key,
            "consultation_status": consultation.status,
            "slot_id": consultation.slot_id,
            "lawyer_id": consultation.lawyer_id,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
        }

        if consultation.slot_id:
            old_slot = await self.slots.get_slot_for_update(
                consultation.slot_id
            )
            if (
                old_slot
                and old_slot.status == "booked"
                and old_slot.consultation_id == consultation.id
            ):
                raise PaymentReviewResolutionError(
                    "У консультации уже есть подтверждённая бронь. "
                    "Используйте решение confirm_existing."
                )
            if old_slot and old_slot.consultation_id == consultation.id:
                await self.slots.release_slot(
                    old_slot.id,
                    consultation.id,
                )
            consultation.slot_id = None
            consultation.scheduled_at = None

        try:
            slot = await self.slots.book_available_slot(
                slot_id=slot_id,
                user_id=case.client_id,
                consultation_id=consultation.id,
            )
        except SlotUnavailableError:
            raise

        consultation.slot_id = slot.id
        consultation.lawyer_id = slot.lawyer_id
        consultation.scheduled_at = slot.starts_at
        consultation.status = ConsultationStatus.BOOKED
        payment.status = PaymentStatus.PAID
        payment.reservation_key = PaymentService.consultation_reservation_key(
            consultation.id,
            slot.id,
        )

        await self._advance_case_after_received_payment(
            case=case,
            actor_id=actor_id,
            comment="Назначен новый слот после ручной проверки платежа",
        )

        await self._record_resolution(
            payment=payment,
            case=case,
            consultation=consultation,
            actor_id=actor_id,
            decision="assign_slot",
            comment=comment,
            old_value=old_value,
            new_value={
                "payment_status": payment.status,
                "consultation_status": consultation.status,
                "slot_id": slot.id,
                "lawyer_id": slot.lawyer_id,
                "scheduled_at": slot.starts_at.isoformat(),
            },
        )
        await self.notifications.emit(
            event_code="CONSULTATION_PAYMENT_REVIEW_RESOLVED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "date": slot.starts_at.strftime("%d.%m.%Y %H:%M"),
            },
            dedupe_key=f"payment-review:{payment.id}:assigned:{slot.id}",
        )
        await self.db.flush()
        return payment, consultation

    async def route_to_refund(
        self,
        *,
        payment_id: int,
        actor_id: int | None,
        comment: str,
    ) -> tuple[Payment, Consultation]:
        comment = self._require_comment(comment)
        payment = await self._lock_payment(payment_id)
        case = await self._lock_case(payment.case_id)
        consultation = await self._lock_latest_consultation(case.id)

        if payment.status == PaymentStatus.REFUND_PENDING:
            return payment, consultation
        if payment.status != PaymentStatus.PAID_REVIEW:
            raise PaymentReviewResolutionError(
                "Платёж не находится в статусе PAID_REVIEW"
            )

        old_value = {
            "payment_status": payment.status,
            "consultation_status": consultation.status,
            "slot_id": consultation.slot_id,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
        }
        if consultation.slot_id:
            slot = await self.slots.get_slot_for_update(consultation.slot_id)
            if slot and slot.consultation_id == consultation.id:
                await self.slots.release_slot(slot.id, consultation.id)

        consultation.slot_id = None
        consultation.scheduled_at = None
        consultation.status = ConsultationStatus.CANCELLED
        payment.status = PaymentStatus.REFUND_PENDING
        case.next_action = "Обработать возврат полученного платежа"

        await self._record_resolution(
            payment=payment,
            case=case,
            consultation=consultation,
            actor_id=actor_id,
            decision="refund_pending",
            comment=comment,
            old_value=old_value,
            new_value={
                "payment_status": payment.status,
                "consultation_status": consultation.status,
            },
        )
        await self.notifications.emit(
            event_code="CONSULTATION_PAYMENT_REVIEW_REFUND_PENDING",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "amount": str(payment.amount),
            },
            dedupe_key=f"payment-review:{payment.id}:refund-pending",
        )
        await self.db.flush()
        return payment, consultation

    async def resolve(
        self,
        *,
        payment_id: int,
        decision: str,
        actor_id: int | None,
        comment: str,
        slot_id: int | None = None,
    ) -> tuple[Payment, Consultation]:
        normalized = str(decision or "").strip().lower()
        if normalized == "confirm_existing":
            return await self.confirm_existing_booking(
                payment_id=payment_id,
                actor_id=actor_id,
                comment=comment,
            )
        if normalized == "assign_slot":
            if not slot_id:
                raise PaymentReviewResolutionError(
                    "Для назначения требуется slot_id"
                )
            return await self.assign_new_slot(
                payment_id=payment_id,
                slot_id=slot_id,
                actor_id=actor_id,
                comment=comment,
            )
        if normalized == "refund_pending":
            return await self.route_to_refund(
                payment_id=payment_id,
                actor_id=actor_id,
                comment=comment,
            )
        raise PaymentReviewResolutionError(
            "Решение должно быть confirm_existing, assign_slot или refund_pending"
        )
