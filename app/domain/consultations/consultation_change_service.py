from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.consultations.consultation_intake import consultation_description_ready
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.payment import Payment


REFUND_RELEVANT_PAYMENT_STATUSES = (
    PaymentStatus.PAID,
    PaymentStatus.REFUND_PENDING,
    PaymentStatus.REFUND_DECLINED,
    PaymentStatus.REFUNDED,
)


class ConsultationChangeService:
    """Client-facing changes to an existing M2 consultation.

    Cancellation is not the same as abandoning the legal request: the booked
    appointment is terminalized (and a paid appointment enters the refund
    workflow), while a fresh consultation record keeps the client's question so
    they can choose another time without re-entering context or documents.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)
        self.consultations = ConsultationService(db)

    async def _has_refund_relevant_payment(self, case_id: int) -> bool:
        payment_id = (
            await self.db.execute(
                select(Payment.id)
                .where(
                    Payment.case_id == case_id,
                    Payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT,
                    Payment.status.in_(REFUND_RELEVANT_PAYMENT_STATUSES),
                )
                .order_by(Payment.created_at.desc(), Payment.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return payment_id is not None

    async def cancel_and_prepare_rebooking(
        self,
        *,
        consultation,
        case,
        client_id: int,
        comment: str,
        payments_currently_disabled: bool,
    ):
        if consultation.case_id != case.id:
            raise ValueError("Консультация не относится к текущему делу")
        if consultation.status != ConsultationStatus.BOOKED:
            raise ValueError("Отменить можно только подтверждённую консультацию")

        old_consultation_id = consultation.id
        description = consultation.client_description
        subject_type = consultation.subject_type or "new_or_other"
        related_case_id = consultation.related_case_id
        has_paid_or_refund_payment = await self._has_refund_relevant_payment(case.id)
        payment_required = has_paid_or_refund_payment or not payments_currently_disabled

        cancelled = await self.consultations.cancel(
            consultation=consultation,
            case=case,
            actor_type="client",
            actor_id=client_id,
            comment=comment,
            payment_required=payment_required,
        )

        replacement = await self.consultations.get_or_create_for_case(case)
        replacement.client_description = description
        replacement.subject_type = subject_type
        replacement.related_case_id = related_case_id
        replacement.status = (
            ConsultationStatus.DOCUMENTS_OPTIONAL
            if consultation_description_ready(replacement)
            else ConsultationStatus.DESCRIPTION_PENDING
        )

        current_case_status = (
            case.status
            if isinstance(case.status, CaseStatus)
            else CaseStatus(str(case.status))
        )
        if current_case_status in {
            CaseStatus.M2_CONSULTATION_BOOKED,
            CaseStatus.M2_PAYMENT_PENDING,
        }:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_SLOT_PENDING,
                actor_type="client",
                actor_id=client_id,
                comment=(
                    "Подтверждённая консультация отменена; вопрос и документы "
                    "сохранены для выбора нового времени"
                ),
            )
        elif current_case_status != CaseStatus.M2_SLOT_PENDING:
            raise ValueError(
                "Текущий этап дела изменился. Откройте карточку и продолжите актуальное действие."
            )

        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=client_id,
            case_id=case.id,
            action="CONSULTATION_REBOOKING_CONTEXT_CREATED",
            old_value={"consultation_id": old_consultation_id},
            new_value={
                "consultation_id": replacement.id,
                "description_preserved": bool(
                    str(replacement.client_description or "").strip()
                ),
                "subject_type": replacement.subject_type,
                "related_case_id": replacement.related_case_id,
                "refund_workflow_required": payment_required,
            },
            comment="После отмены клиент может выбрать новое время без потери контекста",
        )
        await self.db.flush()
        return cancelled, replacement, case, payment_required
