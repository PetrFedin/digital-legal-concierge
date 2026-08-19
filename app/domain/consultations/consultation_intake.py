from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus


M2_INTAKE_CASE_STATUSES = frozenset(
    {
        CaseStatus.M2_CONSULTATION_ROUTE,
        CaseStatus.M2_DESCRIPTION_PENDING,
        CaseStatus.M2_DOCUMENTS_OPTIONAL,
        CaseStatus.M2_SLOT_PENDING,
        CaseStatus.M2_PAYMENT_PENDING,
        CaseStatus.M2_CONSULTATION_BOOKED,
    }
)


class ConsultationIntakeError(ValueError):
    pass


class ActiveCaseRouteConflict(ConsultationIntakeError):
    pass


class ConsultationDescriptionRequired(ConsultationIntakeError):
    pass


def normalized_case_status(case) -> CaseStatus:
    return (
        case.status
        if isinstance(case.status, CaseStatus)
        else CaseStatus(str(case.status))
    )


def consultation_description_ready(consultation) -> bool:
    return len(str(consultation.client_description or "").strip()) >= 20


class ConsultationIntakeService:
    """Keep the Telegram M2 case and consultation state machines in lockstep."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)
        self.consultations = ConsultationService(db)
        self.slots = SlotService(db)
        self.notifications = NotificationEngine(db)

    async def get_or_create_case(
        self,
        client,
        *,
        case_id: int | None = None,
        operation_key: str | None = None,
    ):
        """Resolve an M2 Case without assuming a client-wide singleton.

        ``case_id`` is used by follow-up actions that already know their legal
        matter. ``operation_key`` is used only by an explicit *new consultation*
        action and may create another Case even when the client has other active
        M1/M2 matters. Calls with neither parameter are compatibility/resume
        calls and operate on the selected Telegram cabinet Case.
        """

        if case_id is not None:
            case = await self.cases.get_case_for_user(
                user_id=int(client.id),
                case_id=int(case_id),
            )
            if case is None:
                raise ConsultationIntakeError(
                    "Консультационное обращение не найдено"
                )
        elif operation_key:
            case = await self.cases.create_case_for_operation(
                client=client,
                operation_key=operation_key,
                purpose="m2_consultation_start",
                route=RouteCode.M2,
                status=CaseStatus.M2_DESCRIPTION_PENDING,
                title="Юридическая консультация",
            )
        else:
            case = await self.cases.get_active_case_for_user(int(client.id))
            if case is None:
                # Compatibility bootstrap for legacy entry points. New explicit
                # M2 entry buttons should always pass operation_key so duplicate
                # Telegram delivery is source-idempotent.
                case = await self.cases.get_or_create_active_case_for_user(
                    client,
                    route=RouteCode.M2,
                    status=CaseStatus.M2_DESCRIPTION_PENDING,
                    title="Юридическая консультация",
                )

        status = normalized_case_status(case)
        if str(case.route or "") == RouteCode.M2.value and status in M2_INTAKE_CASE_STATUSES:
            await self.cases.select_case_for_user(
                user_id=int(client.id),
                case_id=int(case.id),
            )
            return case

        raise ActiveCaseRouteConflict(
            "Выбранное обращение относится к другому маршруту. "
            "Откройте нужное дело либо начните новую консультацию отдельным действием."
        )

    async def get_or_create_context(
        self,
        client,
        *,
        case_id: int | None = None,
        operation_key: str | None = None,
    ):
        case = await self.get_or_create_case(
            client,
            case_id=case_id,
            operation_key=operation_key,
        )
        consultation = await self.consultations.get_or_create_for_case(case)
        return case, consultation

    async def save_description(
        self,
        *,
        client,
        description: str,
        subject_type: str,
        related_case_id: int | None,
    ):
        case, consultation = await self.get_or_create_context(client)
        was_booked = consultation.status == ConsultationStatus.BOOKED
        await self.consultations.save_description(
            consultation=consultation,
            case=case,
            client_id=client.id,
            description=description,
            subject_type=subject_type,
            related_case_id=related_case_id,
        )

        status = normalized_case_status(case)
        if status == CaseStatus.M2_CONSULTATION_ROUTE:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_DESCRIPTION_PENDING,
                actor_type="client",
                actor_id=client.id,
                comment="Клиент начал описание вопроса для консультации",
            )
            status = CaseStatus.M2_DESCRIPTION_PENDING
        if status == CaseStatus.M2_DESCRIPTION_PENDING:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_DOCUMENTS_OPTIONAL,
                actor_type="client",
                actor_id=client.id,
                comment="Вопрос консультации сохранён",
            )

        await self.db.flush()
        return case, consultation, was_booked

    async def prepare_slot_selection(self, *, client):
        case, consultation = await self.get_or_create_context(client)
        if consultation.status == ConsultationStatus.BOOKED:
            raise ConsultationIntakeError(
                "Консультация уже подтверждена. Для изменения времени используйте перенос."
            )
        if not consultation_description_ready(consultation):
            raise ConsultationDescriptionRequired(
                "Сначала опишите ситуацию и конкретный вопрос для юриста."
            )

        status = normalized_case_status(case)
        if status == CaseStatus.M2_CONSULTATION_ROUTE:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_DESCRIPTION_PENDING,
                actor_type="client",
                actor_id=client.id,
                comment="Клиент продолжил заполнение консультации",
            )
            status = CaseStatus.M2_DESCRIPTION_PENDING
        if status == CaseStatus.M2_DESCRIPTION_PENDING:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_DOCUMENTS_OPTIONAL,
                actor_type="client",
                actor_id=client.id,
                comment="Описание консультации восстановлено",
            )
            status = CaseStatus.M2_DOCUMENTS_OPTIONAL
        if status == CaseStatus.M2_DOCUMENTS_OPTIONAL:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_SLOT_PENDING,
                actor_type="client",
                actor_id=client.id,
                comment="Клиент перешёл к выбору даты и времени",
            )
            status = CaseStatus.M2_SLOT_PENDING

        if status not in {
            CaseStatus.M2_SLOT_PENDING,
            CaseStatus.M2_PAYMENT_PENDING,
        }:
            raise ConsultationIntakeError(
                "Для текущего этапа выбор времени недоступен. Откройте карточку консультации."
            )
        await self.db.flush()
        return case, consultation

    async def reserve_slot(
        self,
        *,
        client,
        slot_id: int,
        payment_required: bool,
    ):
        case, consultation = await self.prepare_slot_selection(client=client)
        status = normalized_case_status(case)
        if status == CaseStatus.M2_PAYMENT_PENDING:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_SLOT_PENDING,
                actor_type="client",
                actor_id=client.id,
                comment="Клиент выбрал другое время до подтверждения записи",
            )

        consultation, slot = await self.consultations.reserve_slot(
            consultation=consultation,
            case=case,
            client_id=client.id,
            slot_id=slot_id,
        )
        if payment_required:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_PAYMENT_PENDING,
                actor_type="client",
                actor_id=client.id,
                comment="Время удерживается до подтверждения записи",
            )
        await self.db.flush()
        return case, consultation, slot

    async def confirm_without_payment(
        self,
        *,
        client,
        case,
        consultation,
    ):
        if consultation.status == ConsultationStatus.BOOKED:
            if not consultation.slot_id:
                raise ConsultationIntakeError(
                    "У подтверждённой консультации отсутствует слот"
                )
            slot = await self.slots.get_slot(consultation.slot_id)
            if (
                not slot
                or slot.status != "booked"
                or slot.consultation_id != consultation.id
            ):
                raise ConsultationIntakeError("Подтверждённый слот не найден")
            return consultation, slot

        if not consultation_description_ready(consultation):
            raise ConsultationDescriptionRequired(
                "Сначала опишите вопрос для консультации."
            )
        if not consultation.slot_id:
            raise SlotUnavailableError("Сначала выберите свободное время")

        slot = await self.slots.confirm_booking(
            consultation.slot_id,
            consultation.id,
        )
        consultation.status = ConsultationStatus.BOOKED
        consultation.lawyer_id = slot.lawyer_id
        consultation.scheduled_at = slot.starts_at

        if normalized_case_status(case) != CaseStatus.M2_CONSULTATION_BOOKED:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_CONSULTATION_BOOKED,
                actor_type="system",
                actor_id=None,
                comment="Консультация подтверждена без онлайн-оплаты",
            )

        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=case.id,
            action="CONSULTATION_BOOKED_WITHOUT_PAYMENT",
            new_value={
                "consultation_id": consultation.id,
                "slot_id": slot.id,
                "lawyer_id": slot.lawyer_id,
                "scheduled_at": slot.starts_at.isoformat(),
                "payment_provider": "disabled",
            },
            comment="Платёжная ссылка не создавалась: оплата отключена конфигурацией",
        )
        await self.notifications.emit(
            event_code="M2_CONSULTATION_BOOKED",
            case_id=case.id,
            user_id=client.id,
            payload={
                "case_number": case.case_number,
                "date": slot.starts_at.strftime("%d.%m.%Y %H:%M"),
            },
            dedupe_key=f"consultation-booked-no-payment:{consultation.id}:{slot.id}",
        )
        await self.db.flush()
        return consultation, slot
