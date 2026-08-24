from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_lifecycle import PaymentLifecycleService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.payment import Payment
from app.presentation_time import format_business_datetime


INACTIVE_REVIEW_ORIGIN_STATUSES = {
    PaymentStatus.EXPIRED,
    PaymentStatus.CANCELLED,
    PaymentStatus.FAILED,
}


class PaymentReviewResolutionError(ValueError):
    pass


class PaymentReviewConflictError(PaymentReviewResolutionError):
    """A stale review command conflicts with an already persisted decision."""


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

    @staticmethod
    def reservation_context(payment: Payment) -> tuple[int | None, int | None]:
        """Read consultation and slot IDs encoded into an M2 payment key."""

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

    async def _lock_payment_consultation(
        self,
        *,
        payment: Payment,
        case_id: int,
        consultation_id: int | None = None,
    ) -> Consultation:
        """Resolve review context from the payment, with guarded legacy override.

        A parsable reservation key is authoritative and cannot be overridden. For
        legacy payments without a key, a caller may explicitly select a
        consultation in the same case. Without an explicit choice, legacy context
        is accepted only when the case has exactly one consultation.
        """

        linked_consultation_id, _ = self.reservation_context(payment)
        if linked_consultation_id is not None:
            if (
                consultation_id is not None
                and int(consultation_id) != linked_consultation_id
            ):
                raise PaymentReviewResolutionError(
                    "Выбранная консультация не совпадает с привязкой платежа. "
                    "Автоматическое решение заблокировано."
                )
            target_id = linked_consultation_id
        elif consultation_id is not None:
            try:
                target_id = int(consultation_id)
            except (TypeError, ValueError) as error:
                raise PaymentReviewResolutionError(
                    "Некорректный consultation_id для проверки платежа"
                ) from error
            if target_id <= 0:
                raise PaymentReviewResolutionError(
                    "Некорректный consultation_id для проверки платежа"
                )
        else:
            target_id = None

        if target_id is not None:
            consultation = (
                await self.db.execute(
                    select(Consultation)
                    .where(
                        Consultation.id == target_id,
                        Consultation.case_id == case_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not consultation:
                raise PaymentReviewResolutionError(
                    "Платёж ссылается на консультацию, которой нет в этом деле. "
                    "Автоматическое решение заблокировано."
                )
            return consultation

        consultations = list(
            (
                await self.db.execute(
                    select(Consultation)
                    .where(Consultation.case_id == case_id)
                    .order_by(Consultation.created_at.desc(), Consultation.id.desc())
                    .with_for_update()
                )
            ).scalars().all()
        )
        if not consultations:
            raise PaymentReviewResolutionError(
                "По делу не найдена консультация для полученного платежа"
            )
        if len(consultations) != 1:
            raise PaymentReviewResolutionError(
                "У старого платежа нет точной привязки к консультации, а в деле их несколько. "
                "Выберите консультацию после сверки истории."
            )
        return consultations[0]

    async def _latest_review_origin_status(
        self,
        *,
        payment: Payment,
        case_id: int,
    ) -> str | None:
        events = list(
            (
                await self.db.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == case_id,
                        AuditLog.action == "CONSULTATION_PAYMENT_REVIEW_REQUIRED",
                    )
                    .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                    .limit(50)
                )
            ).scalars().all()
        )
        for event in events:
            new_value = event.new_value or {}
            try:
                event_payment_id = int(new_value.get("payment_id") or 0)
            except (TypeError, ValueError):
                event_payment_id = 0
            if event_payment_id != int(payment.id):
                continue
            old_value = event.old_value or {}
            return str(old_value.get("status") or "") or None
        return None

    async def _latest_resolution_event(
        self,
        *,
        payment: Payment,
        case_id: int,
    ) -> AuditLog | None:
        """Return the persisted review decision for this exact payment.

        The payment row is already locked by every caller. Audit history therefore
        acts as the durable idempotency record without adding mutable duplicate
        resolution columns to the financial model.
        """

        events = list(
            (
                await self.db.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == int(case_id),
                        AuditLog.action == "CONSULTATION_PAYMENT_REVIEW_RESOLVED",
                    )
                    .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                    .limit(100)
                )
            ).scalars().all()
        )
        for event in events:
            new_value = event.new_value or {}
            try:
                event_payment_id = int(new_value.get("payment_id") or 0)
            except (TypeError, ValueError):
                event_payment_id = 0
            if event_payment_id == int(payment.id):
                return event
        return None

    @staticmethod
    def _require_comment(comment: str | None) -> str:
        normalized = str(comment or "").strip()
        if len(normalized) < 5:
            raise PaymentReviewResolutionError(
                "Укажите комментарий администратора не короче 5 символов"
            )
        return normalized

    async def _require_exact_retry_or_conflict(
        self,
        *,
        payment: Payment,
        case: Case,
        consultation: Consultation,
        actor_id: int | None,
        decision: str,
        comment: str,
        slot_id: int | None = None,
        compare_slot: bool = False,
    ) -> None:
        """Accept only an exact retry of the already committed review command.

        This distinguishes a network retry from a second administrator acting on
        a stale browser tab. Returning success merely because the payment reached
        PAID/REFUND_PENDING would hide conflicting decisions.
        """

        event = await self._latest_resolution_event(
            payment=payment,
            case_id=int(case.id),
        )
        if event is None:
            raise PaymentReviewConflictError(
                "Платёж уже вышел из очереди сверки другим процессом. Обновите карточку перед новым решением."
            )

        new_value = event.new_value or {}
        actual_decision = str(new_value.get("decision") or "").strip().lower()
        try:
            actual_consultation_id = int(new_value.get("consultation_id") or 0)
        except (TypeError, ValueError):
            actual_consultation_id = 0
        try:
            actual_slot_id = int(new_value.get("slot_id") or 0)
        except (TypeError, ValueError):
            actual_slot_id = 0
        expected_slot_id = int(slot_id or 0)
        actual_comment = str(event.comment or "").strip()
        actual_actor_id = int(event.actor_id) if event.actor_id is not None else None
        expected_actor_id = int(actor_id) if actor_id is not None else None

        same = bool(
            actual_decision == str(decision).strip().lower()
            and actual_consultation_id == int(consultation.id)
            and actual_comment == str(comment).strip()
            and actual_actor_id == expected_actor_id
            and (not compare_slot or actual_slot_id == expected_slot_id)
        )
        if same:
            return

        raise PaymentReviewConflictError(
            "Платёж уже обработан другим или отличающимся решением. Обновите очередь: повторять старую команду автоматически нельзя."
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
        consultation_id: int | None = None,
    ) -> tuple[Payment, Consultation]:
        comment = self._require_comment(comment)
        payment = await self._lock_payment(payment_id)
        case = await self._lock_case(payment.case_id)
        consultation = await self._lock_payment_consultation(
            payment=payment,
            case_id=case.id,
            consultation_id=consultation_id,
        )

        if payment.status == PaymentStatus.PAID:
            await self._require_exact_retry_or_conflict(
                payment=payment,
                case=case,
                consultation=consultation,
                actor_id=actor_id,
                decision="confirm_existing",
                comment=comment,
                slot_id=consultation.slot_id,
                compare_slot=True,
            )
            return payment, consultation
        if payment.status != PaymentStatus.PAID_REVIEW:
            raise PaymentReviewResolutionError(
                "Платёж не находится в статусе PAID_REVIEW"
            )
        origin_status = await self._latest_review_origin_status(
            payment=payment,
            case_id=case.id,
        )
        if origin_status in INACTIVE_REVIEW_ORIGIN_STATUSES:
            raise PaymentReviewResolutionError(
                "Деньги поступили по ранее закрытой или истёкшей ссылке. "
                "Такой платёж нельзя привязать к уже подтверждённой записью; "
                "используйте контролируемый возврат."
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
            "review_origin_status": origin_status,
        }
        PaymentLifecycleService.transition(
            payment,
            to_status=PaymentStatus.PAID,
        )
        payment.reservation_key = PaymentService.consultation_reservation_key(
            consultation.id,
            slot.id,
        )
        if case.status != CaseStatus.M2_CONSULTATION_BOOKED:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_CONSULTATION_BOOKED,
                actor_type="admin",
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
                "date": format_business_datetime(slot.starts_at),
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
        consultation_id: int | None = None,
    ) -> tuple[Payment, Consultation]:
        comment = self._require_comment(comment)
        payment = await self._lock_payment(payment_id)
        case = await self._lock_case(payment.case_id)
        consultation = await self._lock_payment_consultation(
            payment=payment,
            case_id=case.id,
            consultation_id=consultation_id,
        )

        if payment.status == PaymentStatus.PAID:
            await self._require_exact_retry_or_conflict(
                payment=payment,
                case=case,
                consultation=consultation,
                actor_id=actor_id,
                decision="assign_slot",
                comment=comment,
                slot_id=slot_id,
                compare_slot=True,
            )
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
                    "Используйте проверку текущей брони или возврат."
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
        PaymentLifecycleService.transition(
            payment,
            to_status=PaymentStatus.PAID,
        )
        payment.reservation_key = PaymentService.consultation_reservation_key(
            consultation.id,
            slot.id,
        )

        if case.status != CaseStatus.M2_CONSULTATION_BOOKED:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_CONSULTATION_BOOKED,
                actor_type="admin",
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
                "date": format_business_datetime(slot.starts_at),
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
        consultation_id: int | None = None,
    ) -> tuple[Payment, Consultation]:
        comment = self._require_comment(comment)
        payment = await self._lock_payment(payment_id)
        case = await self._lock_case(payment.case_id)
        consultation = await self._lock_payment_consultation(
            payment=payment,
            case_id=case.id,
            consultation_id=consultation_id,
        )

        if payment.status == PaymentStatus.REFUND_PENDING:
            await self._require_exact_retry_or_conflict(
                payment=payment,
                case=case,
                consultation=consultation,
                actor_id=actor_id,
                decision="refund_pending",
                comment=comment,
            )
            return payment, consultation
        if payment.status != PaymentStatus.PAID_REVIEW:
            raise PaymentReviewResolutionError(
                "Платёж не находится в статусе PAID_REVIEW"
            )

        old_value = {
            "payment_status": payment.status,
            "reservation_key": payment.reservation_key,
            "consultation_status": consultation.status,
            "slot_id": consultation.slot_id,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
            "case_status": case.status,
            "case_next_action": case.next_action,
        }

        linked_booking_valid = False
        if consultation.status == ConsultationStatus.BOOKED and consultation.slot_id:
            slot = await self.slots.get_slot_for_update(consultation.slot_id)
            linked_booking_valid = bool(
                slot
                and slot.status == "booked"
                and slot.consultation_id == consultation.id
            )

        case_context_preserved = bool(
            linked_booking_valid
            or str(case.status) == str(CaseStatus.M2_CONSULTATION_BOOKED)
        )

        if not linked_booking_valid:
            if consultation.slot_id:
                slot = await self.slots.get_slot_for_update(consultation.slot_id)
                if slot and slot.consultation_id == consultation.id:
                    await self.slots.release_slot(slot.id, consultation.id)
            consultation.slot_id = None
            consultation.scheduled_at = None
            consultation.status = ConsultationStatus.CANCELLED
            if not case_context_preserved:
                case.next_action = "Обработать возврат полученного платежа"

        PaymentLifecycleService.transition(
            payment,
            to_status=PaymentStatus.REFUND_PENDING,
        )

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
                "slot_id": consultation.slot_id,
                "booking_preserved": linked_booking_valid,
                "case_context_preserved": case_context_preserved,
                "case_status": case.status,
                "case_next_action": case.next_action,
            },
        )
        await self.notifications.emit(
            event_code="CONSULTATION_PAYMENT_REVIEW_REFUND_PENDING",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "amount": str(payment.amount),
                "booking_preserved": linked_booking_valid,
                "case_context_preserved": case_context_preserved,
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
        consultation_id: int | None = None,
    ) -> tuple[Payment, Consultation]:
        normalized = str(decision or "").strip().lower()
        if normalized == "confirm_existing":
            return await self.confirm_existing_booking(
                payment_id=payment_id,
                actor_id=actor_id,
                comment=comment,
                consultation_id=consultation_id,
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
                consultation_id=consultation_id,
            )
        if normalized == "refund_pending":
            return await self.route_to_refund(
                payment_id=payment_id,
                actor_id=actor_id,
                comment=comment,
                consultation_id=consultation_id,
            )
        raise PaymentReviewResolutionError(
            "Решение должно быть confirm_existing, assign_slot или refund_pending"
        )
