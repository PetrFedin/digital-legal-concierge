from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.cancellation_request_service import (
    ConsultationCancellationRequestService,
)
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
)
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment


class CancellationResolutionDecision(StrEnum):
    KEEP_BOOKING = "KEEP_BOOKING"
    REFUND_CONFIRMED = "REFUND_CONFIRMED"


class CancellationResolutionError(RuntimeError):
    """A paid cancellation request cannot be safely resolved."""


@dataclass(frozen=True, slots=True)
class PendingCancellationRequest:
    request_id: int
    case_id: int
    case_number: str
    client_id: int
    consultation_id: int | None
    slot_id: int | None
    created_at: object


class ConsultationCancellationResolutionService:
    REQUEST_ACTION = ConsultationCancellationRequestService.ACTION
    RESOLUTION_ACTION = "CONSULTATION_CANCELLATION_REQUEST_RESOLVED"

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_pending(self, *, limit: int = 100) -> list[PendingCancellationRequest]:
        resolved = exists(
            select(AuditLog.id).where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id == Case.id,
                AuditLog.action == self.RESOLUTION_ACTION,
            )
        )
        rows = list(
            (
                await self.db.execute(
                    select(AuditLog, Case)
                    .join(
                        Case,
                        (AuditLog.entity_type == "case")
                        & (AuditLog.entity_id == Case.id),
                    )
                    .where(
                        AuditLog.action == self.REQUEST_ACTION,
                        ~resolved,
                    )
                    .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                    .limit(max(1, min(limit, 500)))
                )
            ).all()
        )
        result: list[PendingCancellationRequest] = []
        for request, case in rows:
            payload = request.new_value or {}
            result.append(
                PendingCancellationRequest(
                    request_id=request.id,
                    case_id=case.id,
                    case_number=case.case_number,
                    client_id=case.client_id,
                    consultation_id=payload.get("consultation_id"),
                    slot_id=payload.get("slot_id"),
                    created_at=request.created_at,
                )
            )
        return result

    async def resolve(
        self,
        *,
        case_id: int,
        decision: CancellationResolutionDecision | str,
        actor_id: int,
        comment: str,
        source: str = "admin_api",
    ) -> AuditLog:
        try:
            normalized_decision = CancellationResolutionDecision(decision)
        except (TypeError, ValueError) as exc:
            raise CancellationResolutionError(
                "Неизвестное решение по запросу отмены."
            ) from exc
        normalized_comment = " ".join(str(comment or "").split())
        if not normalized_comment:
            raise CancellationResolutionError(
                "Для решения по оплаченной отмене требуется комментарий."
            )

        case = (
            await self.db.execute(
                select(Case)
                .where(
                    Case.id == case_id,
                    Case.route == RouteCode.M2.value,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if case is None:
            raise ConsultationNotFoundError("Консультационное дело не найдено.")

        existing_resolution = (
            await self.db.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case.id,
                    AuditLog.action == self.RESOLUTION_ACTION,
                )
                .order_by(AuditLog.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing_resolution is not None:
            return existing_resolution

        request = (
            await self.db.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case.id,
                    AuditLog.action == self.REQUEST_ACTION,
                )
                .order_by(AuditLog.id.asc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if request is None:
            raise CancellationResolutionError(
                "Запрос клиента на отмену консультации не найден."
            )

        consultation = (
            await self.db.execute(
                select(Consultation)
                .where(
                    Consultation.case_id == case.id,
                    Consultation.status.in_(
                        {
                            ConsultationStatus.PAID_PENDING_CONFIRMATION.value,
                            ConsultationStatus.CONFIRMED.value,
                            ConsultationStatus.BOOKED.value,
                        }
                    ),
                )
                .order_by(Consultation.created_at.desc(), Consultation.id.desc())
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if consultation is None or consultation.slot_id is None:
            raise ActiveConsultationConflictError(
                "Оплаченная активная консультация для запроса не найдена."
            )

        lawyer_id = case.assigned_lawyer_id or consultation.lawyer_id
        if lawyer_id is not None:
            await self.db.execute(
                select(Lawyer.id).where(Lawyer.id == lawyer_id).with_for_update()
            )

        slot = (
            await self.db.execute(
                select(ConsultationSlot)
                .where(
                    ConsultationSlot.id == consultation.slot_id,
                    ConsultationSlot.consultation_id == consultation.id,
                    ConsultationSlot.status == "booked",
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if slot is None:
            raise ActiveConsultationConflictError(
                "Подтверждённый слот оплаченной консультации не найден."
            )

        payments = list(
            (
                await self.db.execute(
                    select(Payment)
                    .where(
                        Payment.case_id == case.id,
                        Payment.payment_code
                        == PaymentCode.M2_CONSULTATION_PAYMENT.value,
                        Payment.status.in_(
                            {
                                PaymentStatus.PAID.value,
                                PaymentStatus.REFUNDED.value,
                            }
                        ),
                    )
                    .order_by(Payment.id.asc())
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if len(payments) != 1:
            raise CancellationResolutionError(
                "Для отмены требуется единственная подтверждённая оплата "
                "или подтверждённый возврат."
            )
        payment = payments[0]

        old_snapshot = {
            "consultation_id": consultation.id,
            "consultation_status": consultation.status,
            "case_status": case.status,
            "next_action": case.next_action,
            "slot_id": slot.id,
            "slot_status": slot.status,
            "payment_id": payment.id,
            "payment_status": payment.status,
        }

        if normalized_decision == CancellationResolutionDecision.KEEP_BOOKING:
            case.next_action = "Ожидайте консультации в выбранное время"
        else:
            if payment.status != PaymentStatus.REFUNDED.value:
                raise CancellationResolutionError(
                    "Нельзя закрыть оплаченную консультацию до подтверждённого "
                    "возврата платежа."
                )
            slot.status = "available"
            slot.hold_expires_at = None
            slot.held_by_user_id = None
            slot.consultation_id = None
            consultation.slot_id = None
            consultation.status = ConsultationStatus.CANCELLED.value
            case.status = CaseStatus.M2_CLOSED.value
            case.next_action = "Консультация отменена после подтверждённого возврата"

        event = await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action=self.RESOLUTION_ACTION,
            old_value=old_snapshot,
            new_value={
                "decision": normalized_decision.value,
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "case_status": case.status,
                "next_action": case.next_action,
                "slot_id": consultation.slot_id,
                "slot_status": slot.status,
                "payment_id": payment.id,
                "payment_status": payment.status,
                "source": source,
            },
            comment=normalized_comment,
        )
        await self.db.flush()
        return event
