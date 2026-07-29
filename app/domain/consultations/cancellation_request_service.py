from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
)
from app.domain.statuses.case_statuses import RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot


class ConsultationCancellationRequestService:
    """Record a client cancellation request without altering a paid booking.

    The project has no automatic refund lifecycle. Releasing a paid booked slot
    immediately would separate money, slot and case state. A client request is
    therefore recorded under the locked Case, while the consultation and slot
    remain unchanged until an authorised employee resolves refund conditions.
    """

    ACTION = "CONSULTATION_CANCELLATION_REQUESTED"
    ALLOWED_STATUSES = frozenset(
        {
            ConsultationStatus.PAID_PENDING_CONFIRMATION.value,
            ConsultationStatus.CONFIRMED.value,
            ConsultationStatus.BOOKED.value,
        }
    )

    def __init__(self, db: AsyncSession):
        self.db = db

    async def request(
        self,
        *,
        consultation: Consultation,
        case: Case,
        client_id: int,
        actor_id: int | None,
        source: str = "telegram",
        reason: str = "Клиент запросил отмену оплаченной консультации",
    ) -> AuditLog:
        if consultation is None or case is None:
            raise ConsultationNotFoundError("Консультация не найдена.")
        if case.client_id != client_id or consultation.case_id != case.id:
            raise ConsultationNotFoundError(
                "Консультация не принадлежит текущему клиенту."
            )

        locked_case = (
            await self.db.execute(
                select(Case)
                .where(
                    Case.id == case.id,
                    Case.client_id == client_id,
                    Case.route == RouteCode.M2.value,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_case is None:
            raise ConsultationNotFoundError("Консультационное дело не найдено.")

        locked_consultation = (
            await self.db.execute(
                select(Consultation)
                .where(
                    Consultation.id == consultation.id,
                    Consultation.case_id == locked_case.id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_consultation is None:
            raise ConsultationNotFoundError("Консультация не найдена.")
        if locked_consultation.status not in self.ALLOWED_STATUSES:
            raise ActiveConsultationConflictError(
                "Запрос отмены недоступен на текущем этапе консультации."
            )
        if locked_consultation.slot_id is None:
            raise ActiveConsultationConflictError(
                "Оплаченная консультация не связана с подтверждённым временем."
            )

        slot = (
            await self.db.execute(
                select(ConsultationSlot)
                .where(
                    ConsultationSlot.id == locked_consultation.slot_id,
                    ConsultationSlot.consultation_id == locked_consultation.id,
                    ConsultationSlot.status == "booked",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if slot is None:
            raise ActiveConsultationConflictError(
                "Подтверждённый слот консультации повреждён."
            )

        existing = (
            await self.db.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == locked_case.id,
                    AuditLog.action == self.ACTION,
                )
                .order_by(AuditLog.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        previous_next_action = locked_case.next_action
        locked_case.next_action = (
            "Ожидайте согласования отмены и условий возможного возврата"
        )
        event = await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=actor_id,
            case_id=locked_case.id,
            action=self.ACTION,
            old_value={
                "consultation_id": locked_consultation.id,
                "consultation_status": locked_consultation.status,
                "case_status": locked_case.status,
                "next_action": previous_next_action,
                "slot_id": slot.id,
            },
            new_value={
                "consultation_id": locked_consultation.id,
                "consultation_status": locked_consultation.status,
                "case_status": locked_case.status,
                "next_action": locked_case.next_action,
                "slot_id": slot.id,
                "source": source,
                "requires_refund_review": True,
            },
            comment=reason,
        )
        await self.db.flush()
        return event
