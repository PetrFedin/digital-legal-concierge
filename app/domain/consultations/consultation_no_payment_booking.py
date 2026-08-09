from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.consultations.consultation_intake import (
    ConsultationDescriptionRequired,
    consultation_description_ready,
)
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.mode import payments_disabled
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.consultation import Consultation


class ConsultationNoPaymentBookingService:
    """Finalize a reserved M2 consultation when online payment is disabled.

    This is intentionally not a fake payment. The held slot is confirmed, the
    consultation/case state machines advance, and notifications are emitted,
    while no Payment row is created or marked PAID.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)
        self.consultations = ConsultationService(db)
        self.notifications = NotificationEngine(db)

    async def _restore_slot_selection_after_hold_loss(self, *, case, error: Exception) -> None:
        if (
            str(case.route or "") == RouteCode.M2.value
            and str(case.status) == CaseStatus.M2_PAYMENT_PENDING.value
        ):
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_SLOT_PENDING,
                actor_type="system",
                actor_id=None,
                comment=(
                    "Резерв консультации больше недоступен. "
                    f"Возвращён выбор времени: {error}"
                ),
            )

    async def confirm(
        self,
        *,
        case,
        client_id: int,
    ) -> Consultation:
        if not payments_disabled():
            raise ValueError(
                "Подтверждение без оплаты доступно только когда онлайн-оплата отключена."
            )
        if str(case.route or "") != RouteCode.M2.value:
            raise ValueError("Текущее дело не относится к маршруту консультации M2.")
        if str(case.status) != CaseStatus.M2_PAYMENT_PENDING.value:
            raise ValueError(
                "Консультация уже изменилась. Откройте актуальную запись перед подтверждением."
            )
        if int(case.client_id or 0) != int(client_id):
            raise ValueError("Консультация принадлежит другому клиенту.")

        current = await self.consultations.get_current_for_case(case.id)
        if not current:
            raise ValueError("Активная консультация не найдена.")
        consultation = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.id == current.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not consultation:
            raise ValueError("Активная консультация не найдена.")
        if consultation.status != ConsultationStatus.PAYMENT_PENDING:
            raise ValueError(
                "Запись уже не ожидает подтверждения. Откройте актуальное состояние консультации."
            )
        if not consultation_description_ready(consultation):
            raise ConsultationDescriptionRequired(
                "Сначала опишите ситуацию и конкретный вопрос для юриста."
            )

        try:
            await self.consultations.require_payable_slot(consultation)
        except SlotUnavailableError as error:
            await self._restore_slot_selection_after_hold_loss(case=case, error=error)
            raise
        slot = await self.consultations.slots.confirm_booking(
            consultation.slot_id,
            consultation.id,
        )
        consultation.status = ConsultationStatus.BOOKED
        consultation.lawyer_id = slot.lawyer_id
        consultation.scheduled_at = slot.starts_at

        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=client_id,
            case_id=case.id,
            action="CONSULTATION_BOOKED_WITHOUT_PAYMENT",
            new_value={
                "consultation_id": consultation.id,
                "slot_id": slot.id,
                "lawyer_id": slot.lawyer_id,
                "scheduled_at": slot.starts_at.isoformat(),
                "payment_required": False,
            },
            comment=(
                "Клиент подтвердил зарезервированное время при отключённой "
                "онлайн-оплате; платёжная запись не создавалась."
            ),
        )
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M2_CONSULTATION_BOOKED,
            actor_type="client",
            actor_id=client_id,
            comment="Клиент подтвердил консультацию без онлайн-оплаты",
        )
        await self.notifications.emit(
            event_code="M2_CONSULTATION_BOOKED",
            case_id=case.id,
            user_id=client_id,
            payload={
                "case_number": case.case_number,
                "date": slot.starts_at.strftime("%d.%m.%Y %H:%M"),
            },
            dedupe_key=f"consultation:{consultation.id}:booked",
        )
        await self.db.flush()
        return consultation
