from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_lifecycle import PaymentLifecycleService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.payment import Payment


class NoShowResolutionError(ValueError):
    pass


class NoShowResolutionService:
    REFUND_ACTION = "CONSULTATION_LAWYER_NO_SHOW_REFUND_REQUESTED"
    LEGACY_REPAIR_ACTION = "CONSULTATION_LAWYER_NO_SHOW_REFUND_LEGACY_REPAIRED"

    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)
        self.cases = CaseService(db)

    async def _advance_refund_case(
        self,
        *,
        case: Case,
        admin_id: int | None,
        comment: str,
    ) -> None:
        status = (
            case.status
            if isinstance(case.status, CaseStatus)
            else CaseStatus(str(case.status))
        )
        if status == CaseStatus.M2_CONSULTATION_BOOKED:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_CONSULTATION_DONE,
                actor_type="admin",
                actor_id=admin_id,
                comment=(
                    "Консультация отменена после подтверждённой неявки юриста; "
                    "ожидается фактический результат возврата"
                ),
            )
        elif status != CaseStatus.M2_CONSULTATION_DONE:
            raise NoShowResolutionError(
                "Статус дела уже изменился. Перед возвратом обновите карточку консультации"
            )
        case.next_action = "Ожидать фактический результат возврата; повторная запись не создаётся автоматически"

    async def _latest_refund_request_event(
        self,
        *,
        case_id: int,
        consultation_id: int,
    ) -> AuditLog | None:
        events = list(
            (
                await self.db.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == int(case_id),
                        AuditLog.action == self.REFUND_ACTION,
                    )
                    .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                )
            ).scalars().all()
        )
        for event in events:
            value = event.new_value or {}
            try:
                event_consultation_id = int(value.get("consultation_id") or 0)
            except (TypeError, ValueError):
                event_consultation_id = 0
            if event_consultation_id == int(consultation_id):
                return event
        return None

    async def _latest_m2_payment_for_update(self, case_id: int) -> Payment | None:
        return (
            await self.db.execute(
                select(Payment)
                .where(
                    Payment.case_id == int(case_id),
                    Payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT,
                )
                .order_by(Payment.created_at.desc(), Payment.id.desc())
                .with_for_update()
            )
        ).scalars().first()

    async def _require_exact_refund_retry_or_conflict(
        self,
        *,
        consultation: Consultation,
        payment: Payment,
        admin_id: int | None,
        comment: str,
    ) -> None:
        event = await self._latest_refund_request_event(
            case_id=int(consultation.case_id),
            consultation_id=int(consultation.id),
        )
        value = event.new_value or {} if event is not None else {}
        try:
            event_payment_id = int(value.get("payment_id") or 0)
        except (TypeError, ValueError):
            event_payment_id = 0
        if (
            event is None
            or event.actor_id != admin_id
            or str(event.comment or "").strip() != comment
            or event_payment_id != int(payment.id)
        ):
            raise NoShowResolutionError(
                "Маршрут возврата уже был создан другим администратором или с другими данными. "
                "Старое действие не применено; обновите карточку."
            )

    async def _release_consultation_slot(self, consultation: Consultation) -> int | None:
        slot_id = int(consultation.slot_id) if consultation.slot_id else None
        if slot_id is None:
            return None
        slot = (
            await self.db.execute(
                select(ConsultationSlot)
                .where(ConsultationSlot.id == slot_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if slot and slot.consultation_id == consultation.id:
            slot.consultation_id = None
            slot.held_by_user_id = None
            slot.hold_expires_at = None
            if str(slot.status) in {"booked", "held", "lawyer_no_show"}:
                slot.status = "available"
        return slot_id

    async def _repair_legacy_cancelled_pending(
        self,
        *,
        consultation: Consultation,
        case: Case,
        payment: Payment,
        admin_id: int | None,
        comment: str,
    ) -> tuple[Consultation, Payment]:
        await self._advance_refund_case(
            case=case,
            admin_id=admin_id,
            comment=comment,
        )
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=admin_id,
            case_id=case.id,
            action=self.LEGACY_REPAIR_ACTION,
            old_value={
                "case_status": CaseStatus.M2_CONSULTATION_BOOKED.value,
                "consultation_status": consultation.status,
                "payment_status": payment.status,
            },
            new_value={
                "case_status": str(case.status),
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "payment_id": payment.id,
                "payment_status": str(payment.status),
                "next_action": case.next_action,
            },
            comment=comment,
        )
        await self.db.flush()
        return consultation, payment

    async def route_lawyer_no_show_to_refund(
        self,
        *,
        consultation_id: int,
        admin_id: int | None,
        comment: str,
    ) -> tuple[Consultation, Payment]:
        normalized_comment = str(comment or "").strip()
        if len(normalized_comment) < 5:
            raise NoShowResolutionError(
                "Укажите комментарий к направлению на возврат"
            )

        consultation = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.id == consultation_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not consultation:
            raise LookupError("Консультация не найдена")

        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == consultation.case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise LookupError("Дело не найдено")

        payment = await self._latest_m2_payment_for_update(int(case.id))
        if not payment:
            raise NoShowResolutionError(
                "Платёж консультации не найден"
            )

        if consultation.status == ConsultationStatus.CANCELLED:
            event = await self._latest_refund_request_event(
                case_id=int(case.id),
                consultation_id=int(consultation.id),
            )
            if event is not None:
                await self._require_exact_refund_retry_or_conflict(
                    consultation=consultation,
                    payment=payment,
                    admin_id=admin_id,
                    comment=normalized_comment,
                )
                # Exact network retry may arrive after the case projection was
                # temporarily stale. Reconcile the projection without creating
                # another payment/audit decision.
                await self._advance_refund_case(
                    case=case,
                    admin_id=admin_id,
                    comment=normalized_comment,
                )
                await self.db.flush()
                return consultation, payment

            if (
                payment.status == PaymentStatus.REFUND_PENDING
                and str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value
            ):
                # Narrow migration-repair path for historical rows created before
                # the atomic no-show resolution contract existed. It is audited
                # explicitly and cannot masquerade as an exact retry afterwards.
                return await self._repair_legacy_cancelled_pending(
                    consultation=consultation,
                    case=case,
                    payment=payment,
                    admin_id=admin_id,
                    comment=normalized_comment,
                )
            raise NoShowResolutionError(
                "Консультация уже отменена, но точное решение о возврате не подтверждается аудитом. "
                "Обновите карточку и проверьте финансовый контур."
            )

        if consultation.status != ConsultationStatus.LAWYER_NO_SHOW:
            raise NoShowResolutionError(
                "Возврат по этой операции доступен только после неявки юриста"
            )
        if payment.status not in {
            PaymentStatus.PAID,
            PaymentStatus.REFUND_PENDING,
        }:
            raise NoShowResolutionError(
                "Оплаченный платёж консультации уже находится в другом финансовом состоянии. "
                "Обновите карточку перед выбором возврата."
            )

        old_value = {
            "case_status": str(case.status),
            "consultation_status": consultation.status,
            "slot_id": consultation.slot_id,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
            "payment_status": payment.status,
        }
        released_slot_id = await self._release_consultation_slot(consultation)

        consultation.slot_id = None
        consultation.scheduled_at = None
        consultation.status = ConsultationStatus.CANCELLED
        if payment.status == PaymentStatus.PAID:
            transition = PaymentLifecycleService.transition(
                payment,
                to_status=PaymentStatus.REFUND_PENDING,
            )
            payment_status = transition.new_status.value
        else:
            payment_status = PaymentStatus.REFUND_PENDING.value
        await self._advance_refund_case(
            case=case,
            admin_id=admin_id,
            comment=normalized_comment,
        )

        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=admin_id,
            case_id=case.id,
            action=self.REFUND_ACTION,
            old_value=old_value,
            new_value={
                "case_status": str(case.status),
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "released_slot_id": released_slot_id,
                "payment_id": payment.id,
                "payment_status": payment_status,
                "next_action": case.next_action,
            },
            comment=normalized_comment,
        )
        await self.notifications.emit(
            event_code="CONSULTATION_CANCELLATION_REQUESTED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "payment_id": payment.id,
                "amount": str(payment.amount),
            },
            dedupe_key=(
                f"consultation:{consultation.id}:lawyer-no-show-refund"
            ),
        )
        await self.db.flush()
        return consultation, payment