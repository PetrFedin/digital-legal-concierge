from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.consultations.consultation_intake import consultation_description_ready
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation


class ClientNoShowResolutionError(ValueError):
    pass


class ClientNoShowResolutionService:
    """Finish the operational branch created by a client no-show.

    A client no-show consumes the original appointment. Rebooking therefore
    creates a fresh consultation record on the same case and deliberately does
    not reuse the old payment/reservation. Closing leaves the no-show record in
    history and closes only the case. Both actions are explicit admin decisions.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)
        self.consultations = ConsultationService(db)
        self.notifications = NotificationEngine(db)

    @staticmethod
    def _comment(value: str | None) -> str:
        comment = str(value or "").strip()
        if len(comment) < 5:
            raise ClientNoShowResolutionError(
                "Укажите комментарий к решению по неявке клиента"
            )
        return comment

    async def _lock_consultation(self, consultation_id: int) -> Consultation:
        consultation = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.id == int(consultation_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if consultation is None:
            raise LookupError("Консультация не найдена")
        return consultation

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

    async def prepare_new_paid_booking(
        self,
        *,
        consultation_id: int,
        admin_id: int | None,
        comment: str,
    ) -> tuple[Consultation, Consultation, Case]:
        normalized_comment = self._comment(comment)
        original = await self._lock_consultation(consultation_id)
        case = await self._lock_case(original.case_id)

        # Idempotent retry after a committed response/UI failure.
        if (
            original.status == ConsultationStatus.RESCHEDULED
            and str(original.decision or "") == "client_no_show_rebook"
        ):
            current = await self.consultations.get_current_for_case(case.id)
            if current is not None:
                return original, current, case
            raise ClientNoShowResolutionError(
                "Перезапись уже была открыта, но текущая консультация не найдена. Требуется ручная проверка."
            )

        if original.status != ConsultationStatus.CLIENT_NO_SHOW:
            raise ClientNoShowResolutionError(
                "Новая запись доступна только после зафиксированной неявки клиента"
            )
        if str(case.route or "") != "M2":
            raise ClientNoShowResolutionError(
                "Дело уже вышло из маршрута консультации. Откройте актуальную карточку."
            )

        old_value = {
            "consultation_id": original.id,
            "consultation_status": str(original.status),
            "case_status": str(case.status),
            "scheduled_at": (
                original.scheduled_at.isoformat() if original.scheduled_at else None
            ),
            "lawyer_id": original.lawyer_id,
        }

        original.status = ConsultationStatus.RESCHEDULED
        original.decision = "client_no_show_rebook"
        await self.db.flush()

        replacement = await self.consultations.get_or_create_for_case(case)
        replacement.consultation_type = str(original.consultation_type or "online")
        replacement.subject_type = str(original.subject_type or "new_or_other")
        replacement.related_case_id = original.related_case_id
        replacement.client_description = original.client_description

        description_ready = consultation_description_ready(replacement)
        replacement.status = (
            ConsultationStatus.DOCUMENTS_OPTIONAL
            if description_ready
            else ConsultationStatus.DESCRIPTION_PENDING
        )
        target_status = (
            CaseStatus.M2_SLOT_PENDING
            if description_ready
            else CaseStatus.M2_DESCRIPTION_PENDING
        )
        current_status = (
            case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
        )
        await self.cases.change_status(
            case=case,
            next_status=target_status,
            actor_type="admin",
            actor_id=admin_id,
            comment=normalized_comment,
            force=(
                target_status == CaseStatus.M2_DESCRIPTION_PENDING
                and current_status == CaseStatus.M2_CONSULTATION_DONE
            ),
        )
        case.next_action = (
            "Клиенту нужно описать вопрос заново перед новой записью"
            if not description_ready
            else "Клиенту нужно выбрать новое время; новая запись оплачивается отдельно"
        )

        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=admin_id,
            case_id=case.id,
            action="CONSULTATION_CLIENT_NO_SHOW_REBOOKING_OPENED",
            old_value=old_value,
            new_value={
                "previous_consultation_id": original.id,
                "previous_consultation_status": str(original.status),
                "consultation_id": replacement.id,
                "consultation_status": str(replacement.status),
                "case_status": str(case.status),
                "description_preserved": description_ready,
                "old_payment_reused": False,
                "next_action": case.next_action,
            },
            comment=normalized_comment,
        )
        await self.notifications.emit(
            event_code="CONSULTATION_CLIENT_NO_SHOW_REBOOKING_OPENED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "consultation_id": replacement.id,
                "payment_reused": False,
            },
            dedupe_key=f"consultation:{original.id}:client-no-show:rebook",
        )
        await self.db.flush()
        return original, replacement, case

    async def close_case(
        self,
        *,
        consultation_id: int,
        admin_id: int | None,
        comment: str,
    ) -> tuple[Consultation, Case]:
        normalized_comment = self._comment(comment)
        consultation = await self._lock_consultation(consultation_id)
        case = await self._lock_case(consultation.case_id)

        case_status = (
            case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
        )
        if (
            consultation.status == ConsultationStatus.CLIENT_NO_SHOW
            and str(consultation.decision or "") == "client_no_show_closed"
            and case_status == CaseStatus.M2_CLOSED
        ):
            return consultation, case

        if consultation.status != ConsultationStatus.CLIENT_NO_SHOW:
            raise ClientNoShowResolutionError(
                "Закрыть по этому основанию можно только после зафиксированной неявки клиента"
            )
        if case_status != CaseStatus.M2_CONSULTATION_DONE:
            raise ClientNoShowResolutionError(
                "Статус дела уже изменился. Откройте актуальную карточку перед закрытием."
            )

        old_value = {
            "consultation_id": consultation.id,
            "consultation_status": str(consultation.status),
            "case_status": str(case.status),
            "decision": consultation.decision,
        }
        consultation.decision = "client_no_show_closed"
        case.close_reason = "M2_CLIENT_NO_SHOW"
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M2_CLOSED,
            actor_type="admin",
            actor_id=admin_id,
            comment=normalized_comment,
        )
        case.next_action = "Обращение закрыто после неявки клиента"

        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=admin_id,
            case_id=case.id,
            action="CONSULTATION_CLIENT_NO_SHOW_CASE_CLOSED",
            old_value=old_value,
            new_value={
                "consultation_id": consultation.id,
                "consultation_status": str(consultation.status),
                "case_status": str(case.status),
                "decision": consultation.decision,
                "close_reason": case.close_reason,
                "payment_changed": False,
            },
            comment=normalized_comment,
        )
        await self.notifications.emit(
            event_code="CONSULTATION_CLIENT_NO_SHOW_CASE_CLOSED",
            case_id=case.id,
            payload={"case_number": case.case_number},
            dedupe_key=f"consultation:{consultation.id}:client-no-show:closed",
        )
        await self.db.flush()
        return consultation, case
