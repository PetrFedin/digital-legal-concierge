from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseAssignmentError, CaseService
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationService,
    ConsultationSlotError,
)
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.consultations.state_machine import (
    ConsultationStateMachine,
    InvalidConsultationTransition,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.lawyer import Lawyer
from app.models.notification import Notification


class ConsultationPaymentLifecycleError(RuntimeError):
    """The M2 payment or confirmation flow cannot safely continue."""


class ConsultationPaymentLifecycleService:
    """Keep M2 slot, payment and lawyer-confirmation states coherent."""

    CLIENT_BOOKED_EVENT = "M2_CONSULTATION_CONFIRMED"

    def __init__(self, db: AsyncSession):
        self.db = db
        self.consultations = ConsultationService(db)

    async def get_single_active_consultation(
        self,
        *,
        case: Case,
        lock: bool = False,
    ) -> Consultation:
        if case is None:
            raise ConsultationPaymentLifecycleError("Дело консультации не найдено.")
        if case.route not in {RouteCode.M2, RouteCode.M2.value}:
            raise ConsultationPaymentLifecycleError(
                "Платёжный сценарий консультации доступен только для маршрута М2."
            )

        if lock:
            locked_case_id = (
                await self.db.execute(
                    select(Case.id).where(Case.id == case.id).with_for_update()
                )
            ).scalar_one_or_none()
            if locked_case_id is None:
                raise ConsultationPaymentLifecycleError("Дело консультации не найдено.")

        query = (
            select(Consultation)
            .where(Consultation.case_id == case.id)
            .where(
                Consultation.status.notin_(ConsultationService.INACTIVE_STATUSES)
            )
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
        )
        if lock:
            query = query.with_for_update()
        active = list((await self.db.execute(query)).scalars().all())
        if not active:
            raise ConsultationPaymentLifecycleError(
                "Активная консультация М2 не найдена."
            )
        if len(active) != 1:
            raise ConsultationPaymentLifecycleError(
                "Для дела найдено несколько активных консультаций М2."
            )
        return active[0]

    async def prepare_payment(
        self,
        *,
        case: Case,
        client_id: int,
        actor_type: str = "client",
        source: str = "telegram",
    ) -> Consultation:
        if case.client_id != client_id:
            raise ConsultationPaymentLifecycleError(
                "Дело не принадлежит текущему клиенту."
            )
        consultation = await self.get_single_active_consultation(
            case=case,
            lock=True,
        )
        try:
            await self.consultations.move_to_payment_pending(
                consultation=consultation,
                case=case,
                actor_type=actor_type,
                actor_id=client_id,
                source=source,
            )
        except (
            ConsultationNotFoundError,
            ActiveConsultationConflictError,
            ConsultationSlotError,
            InvalidConsultationTransition,
        ) as exc:
            raise ConsultationPaymentLifecycleError(str(exc)) from exc
        await self.db.flush()
        return consultation

    async def mark_paid_pending_confirmation(
        self,
        *,
        case: Case,
        source: str = "payment_webhook",
    ) -> Consultation:
        consultation = await self.get_single_active_consultation(
            case=case,
            lock=True,
        )
        try:
            current = ConsultationStatus(consultation.status)
        except (TypeError, ValueError) as exc:
            raise ConsultationPaymentLifecycleError(
                "Неизвестный статус консультации после оплаты."
            ) from exc

        if current == ConsultationStatus.PAID_PENDING_CONFIRMATION:
            return consultation
        if current in {ConsultationStatus.CONFIRMED, ConsultationStatus.BOOKED}:
            return consultation
        if current != ConsultationStatus.PAYMENT_PENDING:
            raise ConsultationPaymentLifecycleError(
                "Оплата получена на недопустимом этапе консультации."
            )
        if consultation.slot_id is None:
            raise ConsultationPaymentLifecycleError(
                "Оплаченная консультация не связана со слотом."
            )

        try:
            slot = await self.consultations.slots.confirm_booking(
                consultation.slot_id,
                consultation.id,
            )
        except SlotUnavailableError as exc:
            raise ConsultationPaymentLifecycleError(
                "Срок удержания слота истёк до подтверждения оплаты."
            ) from exc

        try:
            paid_status = ConsultationStateMachine.transition(
                current,
                ConsultationStatus.PAID_PENDING_CONFIRMATION,
            )
        except InvalidConsultationTransition as exc:
            raise ConsultationPaymentLifecycleError(str(exc)) from exc

        consultation.status = paid_status.value
        consultation.lawyer_id = slot.lawyer_id
        consultation.scheduled_at = slot.starts_at
        case.route = RouteCode.M2.value
        case.status = CaseStatus.M2_PAYMENT_PENDING.value
        case.next_action = "Оплата получена. Ожидайте подтверждения консультации"
        await add_case_history_event(
            self.db,
            actor_type="payment_provider",
            actor_id=None,
            case_id=case.id,
            action="CONSULTATION_PAYMENT_RECEIVED_PENDING_CONFIRMATION",
            old_value={
                "consultation_status": current.value,
                "case_status": CaseStatus.M2_PAYMENT_PENDING.value,
            },
            new_value={
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "case_status": case.status,
                "slot_id": slot.id,
                "lawyer_id": slot.lawyer_id,
                "source": source,
            },
        )
        await self.db.flush()
        return consultation

    async def confirm_by_lawyer(
        self,
        *,
        case: Case,
        lawyer_id: int,
        source: str = "lawyer_api",
    ) -> Consultation:
        consultation = await self.get_single_active_consultation(
            case=case,
            lock=True,
        )
        lawyer = (
            await self.db.execute(
                select(Lawyer).where(
                    Lawyer.id == lawyer_id,
                    Lawyer.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if lawyer is None:
            raise ConsultationPaymentLifecycleError(
                "Активный юрист для подтверждения не найден."
            )
        if consultation.lawyer_id != lawyer_id:
            raise ConsultationPaymentLifecycleError(
                "Консультацию может подтвердить только назначенный на слот юрист."
            )
        if consultation.slot_id is None:
            raise ConsultationPaymentLifecycleError(
                "Консультация не связана с забронированным слотом."
            )
        slot = await self.consultations.slots.get_slot(consultation.slot_id)
        if (
            slot is None
            or slot.status != "booked"
            or slot.consultation_id != consultation.id
            or slot.lawyer_id != lawyer_id
        ):
            raise ConsultationPaymentLifecycleError(
                "Забронированный слот консультации не подтверждён."
            )

        try:
            current = ConsultationStatus(consultation.status)
        except (TypeError, ValueError) as exc:
            raise ConsultationPaymentLifecycleError(
                "Неизвестный статус консультации."
            ) from exc
        if current not in {
            ConsultationStatus.PAID_PENDING_CONFIRMATION,
            ConsultationStatus.CONFIRMED,
            ConsultationStatus.BOOKED,
        }:
            raise ConsultationPaymentLifecycleError(
                "Консультация ещё не оплачена или уже завершена."
            )

        # Assignment is a case-domain invariant. Calling the canonical service
        # here prevents lawyer confirmation from bypassing workload capacity,
        # active-lawyer validation, consultation/slot consistency and audit.
        try:
            await CaseService(self.db).assign_lawyer(
                case=case,
                lawyer_id=lawyer_id,
                actor_id=lawyer_id,
            )
        except CaseAssignmentError as exc:
            raise ConsultationPaymentLifecycleError(
                "Юрист не может подтвердить консультацию: " + str(exc)
            ) from exc

        if current == ConsultationStatus.BOOKED:
            await self._ensure_client_booking_notification(
                case=case,
                consultation=consultation,
                lawyer=lawyer,
            )
            await self.db.flush()
            return consultation

        if current == ConsultationStatus.PAID_PENDING_CONFIRMATION:
            confirmed_status = ConsultationStateMachine.transition(
                current,
                ConsultationStatus.CONFIRMED,
            )
            consultation.status = confirmed_status.value
            await add_case_history_event(
                self.db,
                actor_type="lawyer",
                actor_id=lawyer_id,
                case_id=case.id,
                action="CONSULTATION_CONFIRMED_BY_LAWYER",
                old_value={"consultation_status": current.value},
                new_value={
                    "consultation_id": consultation.id,
                    "consultation_status": consultation.status,
                    "slot_id": slot.id,
                    "source": source,
                },
            )
            current = confirmed_status

        booked_status = ConsultationStateMachine.transition(
            current,
            ConsultationStatus.BOOKED,
        )
        consultation.status = booked_status.value
        consultation.lawyer_id = lawyer_id
        consultation.scheduled_at = slot.starts_at
        case.route = RouteCode.M2.value
        case.status = CaseStatus.M2_CONSULTATION_BOOKED.value
        case.next_action = "Ожидайте консультации в выбранное время"
        await add_case_history_event(
            self.db,
            actor_type="lawyer",
            actor_id=lawyer_id,
            case_id=case.id,
            action="CONSULTATION_BOOKED_AFTER_LAWYER_CONFIRMATION",
            old_value={"consultation_status": current.value},
            new_value={
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "case_status": case.status,
                "slot_id": slot.id,
                "lawyer_id": lawyer_id,
                "scheduled_at": slot.starts_at.isoformat(),
                "source": source,
            },
        )
        await self._ensure_client_booking_notification(
            case=case,
            consultation=consultation,
            lawyer=lawyer,
        )
        await self.db.flush()
        return consultation

    async def _ensure_client_booking_notification(
        self,
        *,
        case: Case,
        consultation: Consultation,
        lawyer: Lawyer,
    ) -> Notification:
        existing = (
            await self.db.execute(
                select(Notification)
                .where(
                    Notification.case_id == case.id,
                    Notification.user_id == case.client_id,
                    Notification.channel == "telegram",
                    Notification.event_code == self.CLIENT_BOOKED_EVENT,
                )
                .order_by(Notification.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        scheduled_at = consultation.scheduled_at
        scheduled_text = (
            scheduled_at.isoformat()
            if scheduled_at is not None
            else "время будет уточнено"
        )
        notification = Notification(
            case_id=case.id,
            user_id=case.client_id,
            channel="telegram",
            event_code=self.CLIENT_BOOKED_EVENT,
            title="Консультация подтверждена",
            text=(
                f"Юрист {lawyer.full_name} подтвердил консультацию по делу "
                f"{case.case_number}. Дата и время: {scheduled_text}."
            ),
            status="PENDING",
            is_sent=False,
        )
        self.db.add(notification)
        await self.db.flush()
        return notification
