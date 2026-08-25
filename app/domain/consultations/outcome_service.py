from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.presentation_time import format_business_datetime


class ConsultationOutcomeError(ValueError):
    pass


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ConsultationOutcomeService:
    NO_SHOW_DELAY = timedelta(minutes=15)
    VALID_DECISIONS = {"close", "to_m1", "follow_up", "other"}

    def __init__(self, db: AsyncSession):
        self.db = db
        self.slots = SlotService(db)
        self.cases = CaseService(db)
        self.notifications = NotificationEngine(db)

    async def _lock_consultation(self, consultation_id: int) -> Consultation:
        consultation = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.id == consultation_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not consultation:
            raise LookupError("Консультация не найдена")
        return consultation

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

    async def _lock_slot(
        self,
        consultation: Consultation,
    ) -> ConsultationSlot:
        if not consultation.slot_id:
            raise ConsultationOutcomeError(
                "У консультации отсутствует связанный слот"
            )
        slot = await self.slots.get_slot_for_update(consultation.slot_id)
        if not slot or slot.consultation_id != consultation.id:
            raise ConsultationOutcomeError(
                "Связь консультации со слотом повреждена"
            )
        return slot

    @staticmethod
    def _require_assigned_lawyer(
        consultation: Consultation,
        lawyer_id: int,
    ) -> None:
        if consultation.lawyer_id != lawyer_id:
            raise ConsultationOutcomeError(
                "Действие доступно только назначенному юристу"
            )

    @staticmethod
    def _ensure_started(slot: ConsultationSlot) -> None:
        if as_utc(slot.starts_at) > datetime.now(timezone.utc):
            raise ConsultationOutcomeError(
                "Нельзя зафиксировать результат до начала консультации"
            )

    @classmethod
    def _ensure_no_show_allowed(cls, slot: ConsultationSlot) -> None:
        threshold = as_utc(slot.starts_at) + cls.NO_SHOW_DELAY
        if datetime.now(timezone.utc) < threshold:
            raise ConsultationOutcomeError(
                "Неявку можно зафиксировать не ранее чем через 15 минут после начала"
            )

    async def complete(
        self,
        *,
        consultation_id: int,
        lawyer_id: int,
        result: str,
        decision: str,
    ) -> Consultation:
        normalized_result = str(result or "").strip()
        normalized_decision = str(decision or "").strip().lower()
        if len(normalized_result) < 20:
            raise ConsultationOutcomeError(
                "Опишите результат консультации не короче 20 символов"
            )
        if normalized_decision not in self.VALID_DECISIONS:
            raise ConsultationOutcomeError(
                "Решение должно быть close, to_m1, follow_up или other"
            )

        consultation = await self._lock_consultation(consultation_id)
        self._require_assigned_lawyer(consultation, lawyer_id)
        if consultation.status == ConsultationStatus.DONE:
            stored_result = str(consultation.lawyer_result or "").strip()
            stored_decision = str(consultation.decision or "").strip().lower()
            if (
                stored_result != normalized_result
                or stored_decision != normalized_decision
            ):
                raise ConsultationOutcomeError(
                    "Консультация уже завершена другим итогом. Старое действие не применено; обновите карточку."
                )
            return consultation
        if consultation.status != ConsultationStatus.BOOKED:
            raise ConsultationOutcomeError(
                "Завершить можно только подтверждённую консультацию"
            )

        case = await self._lock_case(consultation.case_id)
        slot = await self._lock_slot(consultation)
        self._ensure_started(slot)
        old_value = {
            "consultation_status": consultation.status,
            "case_status": case.status,
            "slot_status": slot.status,
        }

        consultation.status = ConsultationStatus.DONE
        consultation.lawyer_result = normalized_result
        consultation.decision = normalized_decision
        slot.status = "completed"
        slot.hold_expires_at = None

        if normalized_decision == "to_m1":
            await self.cases.transfer_to_m1(
                case=case,
                actor_type="lawyer",
                actor_id=lawyer_id,
                comment="Перевод в маршрут М1 по результату консультации",
            )
        elif normalized_decision == "close":
            case.close_reason = "M2_CONSULTATION_COMPLETED"
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_CLOSED,
                actor_type="lawyer",
                actor_id=lawyer_id,
                comment="Консультация завершена, обращение закрыто",
            )
        else:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_CONSULTATION_DONE,
                actor_type="lawyer",
                actor_id=lawyer_id,
                comment="Результат консультации зафиксирован",
            )
            case.next_action = (
                "Назначить следующую консультацию"
                if normalized_decision == "follow_up"
                else "Проверить решение юриста и следующий шаг"
            )

        await add_case_history_event(
            self.db,
            actor_type="lawyer",
            actor_id=lawyer_id,
            case_id=case.id,
            action="CONSULTATION_COMPLETED",
            old_value=old_value,
            new_value={
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "slot_status": slot.status,
                "decision": normalized_decision,
                "result": normalized_result,
                "case_status": case.status,
                "close_reason": case.close_reason,
                "next_action": case.next_action,
            },
        )
        await self.notifications.emit(
            event_code="CONSULTATION_COMPLETED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "decision": normalized_decision,
            },
            dedupe_key=f"consultation:{consultation.id}:completed",
        )
        await self.db.flush()
        return consultation

    async def mark_client_no_show(
        self,
        *,
        consultation_id: int,
        lawyer_id: int,
        comment: str,
    ) -> Consultation:
        normalized_comment = str(comment or "").strip()
        if len(normalized_comment) < 5:
            raise ConsultationOutcomeError(
                "Укажите комментарий о неявке клиента"
            )

        consultation = await self._lock_consultation(consultation_id)
        self._require_assigned_lawyer(consultation, lawyer_id)
        if consultation.status == ConsultationStatus.CLIENT_NO_SHOW:
            stored_comment = str(consultation.lawyer_result or "").strip()
            stored_decision = str(consultation.decision or "").strip().lower()
            if stored_decision != "client_no_show" or stored_comment != normalized_comment:
                raise ConsultationOutcomeError(
                    "Неявка клиента уже зафиксирована с другим комментарием. Старое действие не применено; обновите карточку."
                )
            return consultation
        if consultation.status != ConsultationStatus.BOOKED:
            raise ConsultationOutcomeError(
                "Неявку можно отметить только по подтверждённой консультации"
            )

        case = await self._lock_case(consultation.case_id)
        slot = await self._lock_slot(consultation)
        self._ensure_no_show_allowed(slot)
        old_value = {
            "consultation_status": consultation.status,
            "case_status": case.status,
            "slot_status": slot.status,
        }

        consultation.status = ConsultationStatus.CLIENT_NO_SHOW
        consultation.decision = "client_no_show"
        consultation.lawyer_result = normalized_comment
        slot.status = "client_no_show"
        slot.hold_expires_at = None
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M2_CONSULTATION_DONE,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment="Юрист зафиксировал неявку клиента",
        )
        case.next_action = (
            "Связаться с клиентом и определить перенос или закрытие обращения"
        )

        await add_case_history_event(
            self.db,
            actor_type="lawyer",
            actor_id=lawyer_id,
            case_id=case.id,
            action="CONSULTATION_CLIENT_NO_SHOW",
            old_value=old_value,
            new_value={
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "slot_status": slot.status,
                "comment": normalized_comment,
                "next_action": case.next_action,
            },
        )
        await self.notifications.emit(
            event_code="CONSULTATION_CLIENT_NO_SHOW",
            case_id=case.id,
            payload={"case_number": case.case_number},
            dedupe_key=f"consultation:{consultation.id}:client-no-show",
        )
        await self.db.flush()
        return consultation

    async def mark_lawyer_no_show(
        self,
        *,
        consultation_id: int,
        admin_id: int | None,
        comment: str,
    ) -> Consultation:
        normalized_comment = str(comment or "").strip()
        if len(normalized_comment) < 5:
            raise ConsultationOutcomeError(
                "Укажите комментарий о неявке юриста"
            )

        consultation = await self._lock_consultation(consultation_id)
        if consultation.status == ConsultationStatus.LAWYER_NO_SHOW:
            stored_comment = str(consultation.lawyer_result or "").strip()
            stored_decision = str(consultation.decision or "").strip().lower()
            if stored_decision != "lawyer_no_show" or stored_comment != normalized_comment:
                raise ConsultationOutcomeError(
                    "Неявка юриста уже зафиксирована с другим комментарием. Старое действие не применено; обновите карточку."
                )
            return consultation
        if consultation.status != ConsultationStatus.BOOKED:
            raise ConsultationOutcomeError(
                "Неявку можно отметить только по подтверждённой консультации"
            )

        case = await self._lock_case(consultation.case_id)
        slot = await self._lock_slot(consultation)
        self._ensure_no_show_allowed(slot)
        old_value = {
            "consultation_status": consultation.status,
            "case_status": case.status,
            "slot_status": slot.status,
            "lawyer_id": consultation.lawyer_id,
        }

        consultation.status = ConsultationStatus.LAWYER_NO_SHOW
        consultation.decision = "lawyer_no_show"
        consultation.lawyer_result = normalized_comment
        slot.status = "lawyer_no_show"
        slot.hold_expires_at = None
        case.next_action = (
            "Срочно предложить клиенту бесплатный перенос или возврат"
        )

        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=admin_id,
            case_id=case.id,
            action="CONSULTATION_LAWYER_NO_SHOW",
            old_value=old_value,
            new_value={
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "slot_status": slot.status,
                "comment": normalized_comment,
                "next_action": case.next_action,
            },
        )
        await self.notifications.emit(
            event_code="CONSULTATION_LAWYER_NO_SHOW",
            case_id=case.id,
            payload={"case_number": case.case_number},
            dedupe_key=f"consultation:{consultation.id}:lawyer-no-show",
        )
        await self.db.flush()
        return consultation

    async def rebook_after_lawyer_no_show(
        self,
        *,
        consultation_id: int,
        new_slot_id: int,
        admin_id: int | None,
        comment: str,
    ) -> Consultation:
        normalized_comment = str(comment or "").strip()
        if len(normalized_comment) < 5:
            raise ConsultationOutcomeError(
                "Укажите комментарий к бесплатному переносу"
            )

        consultation = await self._lock_consultation(consultation_id)
        if consultation.status != ConsultationStatus.LAWYER_NO_SHOW:
            raise ConsultationOutcomeError(
                "Бесплатный перенос доступен только после неявки юриста"
            )
        case = await self._lock_case(consultation.case_id)
        old_slot = await self._lock_slot(consultation)
        old_value = {
            "consultation_status": consultation.status,
            "slot_id": old_slot.id,
            "slot_status": old_slot.status,
            "lawyer_id": consultation.lawyer_id,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
        }

        old_slot.consultation_id = None
        old_slot.held_by_user_id = None
        consultation.slot_id = None
        consultation.scheduled_at = None
        await self.db.flush()

        try:
            new_slot = await self.slots.book_available_slot(
                slot_id=new_slot_id,
                user_id=case.client_id,
                consultation_id=consultation.id,
            )
        except SlotUnavailableError:
            raise

        consultation.slot_id = new_slot.id
        consultation.lawyer_id = new_slot.lawyer_id
        consultation.scheduled_at = new_slot.starts_at
        consultation.status = ConsultationStatus.BOOKED
        consultation.decision = None
        consultation.lawyer_result = None
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M2_CONSULTATION_BOOKED,
            actor_type="admin",
            actor_id=admin_id,
            comment="Бесплатный перенос после неявки юриста",
        )

        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=admin_id,
            case_id=case.id,
            action="CONSULTATION_REBOOKED_AFTER_LAWYER_NO_SHOW",
            old_value=old_value,
            new_value={
                "consultation_id": consultation.id,
                "consultation_status": consultation.status,
                "slot_id": new_slot.id,
                "lawyer_id": new_slot.lawyer_id,
                "scheduled_at": new_slot.starts_at.isoformat(),
            },
            comment=normalized_comment,
        )
        await self.notifications.emit(
            event_code="CONSULTATION_LAWYER_NO_SHOW_REBOOKED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "date": format_business_datetime(new_slot.starts_at),
            },
            dedupe_key=(
                f"consultation:{consultation.id}:lawyer-no-show-rebook:{new_slot.id}"
            ),
        )
        await self.db.flush()
        return consultation