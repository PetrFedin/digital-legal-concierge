from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.consultations.state_machine import ConsultationStateMachine
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot


class ConsultationNotFoundError(LookupError):
    """The consultation is missing or does not belong to the supplied case."""


class ConsultationDescriptionError(ValueError):
    """The consultation description is empty, too long, or cannot be changed."""


class ConsultationSlotError(RuntimeError):
    """A consultation slot cannot be safely held or used for payment."""


class ActiveConsultationConflictError(RuntimeError):
    """The case route or its active consultations are contradictory."""


class ConsultationService:
    DESCRIPTION_MAX_LENGTH = 4000
    INACTIVE_STATUSES = frozenset(
        {
            ConsultationStatus.DECLINED.value,
            ConsultationStatus.DONE.value,
            ConsultationStatus.CANCELLED.value,
            ConsultationStatus.CLOSED.value,
        }
    )
    # Consultation.status is the detailed M2 workflow. Case.status is the
    # coarse-grained client-facing state of the legal case.
    _CASE_STATE_BY_CONSULTATION = {
        ConsultationStatus.DESCRIPTION_PENDING: (
            CaseStatus.M2_DESCRIPTION_PENDING,
            "Опишите вопрос для юриста",
        ),
        ConsultationStatus.DOCUMENTS_OPTIONAL: (
            CaseStatus.M2_DOCUMENTS_OPTIONAL,
            "Загрузите документы или пропустите этот шаг",
        ),
        ConsultationStatus.SLOT_PENDING: (
            CaseStatus.M2_SLOT_PENDING,
            "Выберите удобное время консультации",
        ),
        ConsultationStatus.SLOT_RESERVED: (
            CaseStatus.M2_PAYMENT_PENDING,
            "Перейдите к оплате консультации",
        ),
        ConsultationStatus.PAYMENT_PENDING: (
            CaseStatus.M2_PAYMENT_PENDING,
            "Оплатите консультацию для подтверждения записи",
        ),
    }

    def __init__(self, db: AsyncSession):
        self.db = db
        self.slots = SlotService(db)

    async def create_or_get_m2_consultation(
        self,
        *,
        case,
        actor_type: str = "system",
        actor_id: int | None = None,
        source: str = "domain",
    ) -> Consultation:
        if case is None:
            raise ConsultationNotFoundError("Дело для консультации не найдено.")
        if case.route not in {None, RouteCode.M2, RouteCode.M2.value}:
            raise ActiveConsultationConflictError(
                "Активное дело другого маршрута нельзя автоматически перевести в М2."
            )

        # PostgreSQL locks this exact case row before the active-consultation
        # lookup. SQLite accepts the query but ignores FOR UPDATE, so it does
        # not provide the same cross-transaction creation guarantee there.
        locked_case_id = (
            await self.db.execute(
                select(Case.id).where(Case.id == case.id).with_for_update()
            )
        ).scalar_one_or_none()
        if locked_case_id is None:
            raise ConsultationNotFoundError("Дело для консультации не найдено.")

        result = await self.db.execute(
            select(Consultation)
            .where(Consultation.case_id == case.id)
            .where(Consultation.status.notin_(self.INACTIVE_STATUSES))
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
        )
        active = list(result.scalars().all())
        if len(active) > 1:
            raise ActiveConsultationConflictError(
                "Для дела найдено несколько активных консультаций М2."
            )
        if active:
            return active[0]

        consultation = Consultation(
            case_id=case.id,
            status=ConsultationStatus.DESCRIPTION_PENDING.value,
        )
        self.db.add(consultation)
        await self.db.flush()
        self._sync_case_from_consultation(case, consultation.status)
        await self._add_history(
            case=case,
            consultation=consultation,
            action="M2_CONSULTATION_CREATED",
            actor_type=actor_type,
            actor_id=actor_id,
            source=source,
        )
        return consultation

    async def get_or_create_for_case(self, case):
        """Preserve the legacy lookup semantics until callers move to the M2 flow."""
        result = await self.db.execute(
            select(Consultation)
            .where(Consultation.case_id == case.id)
            .order_by(Consultation.created_at.desc())
        )
        consultation = result.scalars().first()
        if consultation and consultation.status not in {
            ConsultationStatus.DONE,
            ConsultationStatus.CANCELLED,
        }:
            return consultation
        consultation = Consultation(
            case_id=case.id,
            status=ConsultationStatus.DESCRIPTION_PENDING.value,
        )
        self.db.add(consultation)
        await self.db.flush()
        return consultation

    async def save_description(
        self,
        *,
        consultation,
        case,
        client_id: int | None,
        description: str,
        subject_type: str = "new_or_other",
        related_case_id: int | None = None,
        actor_type: str = "client",
        source: str = "telegram",
    ) -> Consultation:
        self._ensure_consultation_belongs_to_case(consultation, case)
        normalized = (description or "").strip()
        if not normalized:
            raise ConsultationDescriptionError(
                "Описание консультации не может быть пустым."
            )
        if len(normalized) > self.DESCRIPTION_MAX_LENGTH:
            raise ConsultationDescriptionError(
                f"Описание консультации не должно превышать "
                f"{self.DESCRIPTION_MAX_LENGTH} символов."
            )

        current = ConsultationStatus(consultation.status)
        if current == ConsultationStatus.BOOKED and not consultation.client_description:
            # Compatibility for the current Telegram flow, which collects the
            # question after booking. It intentionally leaves BOOKED unchanged.
            consultation.client_description = normalized
            consultation.subject_type = subject_type
            consultation.related_case_id = related_case_id
            await self._add_history(
                case=case,
                consultation=consultation,
                action="CONSULTATION_DESCRIPTION_SAVED",
                actor_type=actor_type,
                actor_id=client_id,
                source=source,
                details={
                    "description_length": len(normalized),
                    "subject_type": subject_type,
                    "related_case_id": related_case_id,
                },
            )
            return consultation
        if current != ConsultationStatus.DESCRIPTION_PENDING:
            if consultation.client_description == normalized:
                return consultation
            raise ConsultationDescriptionError(
                "Описание уже сохранено; изменение после завершения шага "
                "требует отдельной операции."
            )

        validated_status = ConsultationStateMachine.transition(
            current,
            ConsultationStatus.DOCUMENTS_OPTIONAL,
        )
        consultation.client_description = normalized
        consultation.subject_type = subject_type
        consultation.related_case_id = related_case_id
        consultation.status = validated_status.value
        self._sync_case_from_consultation(case, validated_status)
        await self._add_history(
            case=case,
            consultation=consultation,
            action="CONSULTATION_DESCRIPTION_SAVED",
            actor_type=actor_type,
            actor_id=client_id,
            source=source,
            details={
                "description_length": len(normalized),
                "subject_type": subject_type,
                "related_case_id": related_case_id,
            },
        )
        return consultation

    async def complete_documents_step(
        self,
        *,
        consultation,
        case,
        documents_uploaded: bool,
        actor_type: str = "client",
        actor_id: int | None = None,
        source: str = "telegram",
    ) -> Consultation:
        self._ensure_consultation_belongs_to_case(consultation, case)
        current = ConsultationStatus(consultation.status)
        if current == ConsultationStatus.SLOT_PENDING:
            return consultation

        validated_status = ConsultationStateMachine.transition(
            current,
            ConsultationStatus.SLOT_PENDING,
        )
        consultation.status = validated_status.value
        self._sync_case_from_consultation(case, validated_status)
        action = (
            "CONSULTATION_DOCUMENTS_COMPLETED"
            if documents_uploaded
            else "CONSULTATION_DOCUMENTS_SKIPPED"
        )
        await self._add_history(
            case=case,
            consultation=consultation,
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            source=source,
            details={"documents_uploaded": documents_uploaded},
        )
        return consultation

    async def reserve_pre_payment_slot(
        self,
        *,
        consultation,
        case,
        client_id: int,
        slot_id: int,
        actor_type: str = "client",
        source: str = "telegram",
    ):
        self._ensure_consultation_belongs_to_case(consultation, case)
        current = ConsultationStatus(consultation.status)

        if consultation.slot_id is not None:
            if consultation.slot_id != slot_id:
                raise ConsultationSlotError(
                    "За консультацией уже удерживается другой слот."
                )
            existing = await self.slots.get_slot(slot_id)
            if self._is_current_hold(existing, consultation, client_id):
                return consultation, existing
            raise ConsultationSlotError(
                "Связанный резерв слота отсутствует или истёк."
            )

        validated_status = ConsultationStateMachine.transition(
            current,
            ConsultationStatus.SLOT_RESERVED,
        )
        slot = await self.slots.get_slot(slot_id)
        if slot is None:
            raise ConsultationSlotError("Слот консультации не найден.")
        if slot.status != "available" or not self._is_future(slot.starts_at):
            raise ConsultationSlotError("Слот недоступен для резервирования.")

        try:
            async with self.db.begin_nested():
                slot = await self.slots.hold_slot(
                    slot_id,
                    client_id,
                    consultation.id,
                )
                consultation.slot_id = slot.id
                consultation.lawyer_id = slot.lawyer_id
                consultation.scheduled_at = slot.starts_at
                consultation.status = validated_status.value
                self._sync_case_from_consultation(case, validated_status)
                await self._add_history(
                    case=case,
                    consultation=consultation,
                    action="CONSULTATION_SLOT_RESERVED",
                    actor_type=actor_type,
                    actor_id=client_id,
                    source=source,
                    details={
                        "slot_id": slot.id,
                        "lawyer_id": slot.lawyer_id,
                        "scheduled_at": slot.starts_at.isoformat(),
                        "hold_expires_at": (
                            slot.hold_expires_at.isoformat()
                            if slot.hold_expires_at
                            else None
                        ),
                    },
                )
        except SlotUnavailableError as exc:
            raise ConsultationSlotError(
                "Слот стал недоступен во время резервирования."
            ) from exc
        return consultation, slot

    async def reserve_slot(
        self,
        *,
        consultation,
        case,
        client_id: int,
        slot_id: int,
    ):
        """Legacy slot selection retained until Telegram uses M2 pre-payment."""
        previous_slot_id = consultation.slot_id
        if previous_slot_id == slot_id:
            slot = await self.slots.get_slot(slot_id)
            if (
                slot
                and slot.consultation_id == consultation.id
                and slot.status in {"held", "booked"}
            ):
                return consultation, slot
        slot = await self.slots.hold_slot(slot_id, client_id, consultation.id)
        if previous_slot_id and previous_slot_id != slot.id:
            await self.slots.release_slot(previous_slot_id, consultation.id)
        consultation.slot_id = slot.id
        consultation.lawyer_id = slot.lawyer_id
        consultation.scheduled_at = slot.starts_at
        consultation.status = ConsultationStatus.PAYMENT_PENDING.value
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=client_id,
            case_id=case.id,
            action="CONSULTATION_SLOT_HELD",
            new_value={
                "slot_id": slot.id,
                "lawyer_id": slot.lawyer_id,
                "scheduled_at": slot.starts_at.isoformat(),
                "hold_expires_at": (
                    slot.hold_expires_at.isoformat()
                    if slot.hold_expires_at
                    else None
                ),
            },
        )
        await self.db.flush()
        return consultation, slot

    async def move_to_payment_pending(
        self,
        *,
        consultation,
        case,
        actor_type: str = "client",
        actor_id: int | None = None,
        source: str = "telegram",
    ) -> Consultation:
        self._ensure_consultation_belongs_to_case(consultation, case)
        current = ConsultationStatus(consultation.status)
        if current not in {
            ConsultationStatus.SLOT_RESERVED,
            ConsultationStatus.PAYMENT_PENDING,
        }:
            ConsultationStateMachine.validate_transition(
                current,
                ConsultationStatus.PAYMENT_PENDING,
            )
        if consultation.slot_id is None:
            raise ConsultationSlotError(
                "Для перехода к оплате требуется удерживаемый слот."
            )
        slot = await self.slots.get_slot(consultation.slot_id)
        if not self._is_current_hold(slot, consultation, case.client_id):
            raise ConsultationSlotError(
                "Удерживаемый слот отсутствует или срок резерва истёк."
            )
        if current == ConsultationStatus.PAYMENT_PENDING:
            return consultation

        validated_status = ConsultationStateMachine.transition(
            current,
            ConsultationStatus.PAYMENT_PENDING,
        )
        consultation.status = validated_status.value
        self._sync_case_from_consultation(case, validated_status)
        await self._add_history(
            case=case,
            consultation=consultation,
            action="CONSULTATION_PAYMENT_PENDING",
            actor_type=actor_type,
            actor_id=actor_id,
            source=source,
            details={"slot_id": slot.id},
        )
        return consultation

    async def mark_booked_after_payment(self, *, consultation, case):
        if not consultation.slot_id:
            raise ValueError("Для консультации не выбран слот")
        slot = await self.slots.confirm_booking(
            consultation.slot_id,
            consultation.id,
        )
        consultation.status = ConsultationStatus.BOOKED.value
        consultation.lawyer_id = slot.lawyer_id
        consultation.scheduled_at = slot.starts_at
        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=case.id,
            action="CONSULTATION_BOOKED_AFTER_PAYMENT",
            new_value={
                "consultation_id": consultation.id,
                "slot_id": slot.id,
                "lawyer_id": slot.lawyer_id,
                "scheduled_at": slot.starts_at.isoformat(),
            },
        )
        await self.db.flush()
        return consultation

    async def cancel(
        self,
        *,
        consultation,
        case,
        actor_type: str,
        actor_id: int | None,
        comment: str,
    ) -> Consultation:
        """Cancel a consultation and release only its linked slot atomically.

        The caller controls the outer transaction. Repeating cancellation is
        idempotent; completed and declined consultations cannot be reopened or
        cancelled through this operation.
        """
        self._ensure_consultation_belongs_to_case(consultation, case)
        locked = (
            await self.db.execute(
                select(Consultation)
                .where(
                    Consultation.id == consultation.id,
                    Consultation.case_id == case.id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            raise ConsultationNotFoundError("Консультация не найдена.")

        try:
            current = ConsultationStatus(locked.status)
        except (TypeError, ValueError) as exc:
            raise ActiveConsultationConflictError(
                "Неизвестный статус консультации не допускает отмену."
            ) from exc
        if current == ConsultationStatus.CANCELLED:
            return locked
        if current in {
            ConsultationStatus.DONE,
            ConsultationStatus.CLOSED,
            ConsultationStatus.DECLINED,
        }:
            raise ActiveConsultationConflictError(
                "Завершённую консультацию нельзя отменить."
            )

        previous_slot_id = locked.slot_id
        previous_case_status = case.status
        if previous_slot_id is not None:
            slot = (
                await self.db.execute(
                    select(ConsultationSlot)
                    .where(
                        ConsultationSlot.id == previous_slot_id,
                        ConsultationSlot.consultation_id == locked.id,
                    )
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if slot is None:
                raise ConsultationSlotError(
                    "Связанный слот консультации не найден или принадлежит "
                    "другой записи."
                )
            await self.slots.release_slot(previous_slot_id, locked.id)
            locked.slot_id = None

        locked.status = ConsultationStatus.CANCELLED.value
        if case.route in {RouteCode.M2, RouteCode.M2.value}:
            case.status = CaseStatus.M2_CLOSED.value
            case.next_action = "Консультация отменена"
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CONSULTATION_CANCELLED",
            old_value={
                "consultation_id": locked.id,
                "consultation_status": current.value,
                "case_status": previous_case_status,
                "slot_id": previous_slot_id,
            },
            new_value={
                "consultation_id": locked.id,
                "consultation_status": locked.status,
                "case_status": case.status,
                "slot_id": None,
            },
            comment=comment,
        )
        await self.db.flush()
        return locked

    async def mark_done(
        self,
        *,
        consultation,
        case,
        lawyer_id: int,
        result: str,
        decision: str,
    ):
        consultation.status = ConsultationStatus.DONE.value
        consultation.lawyer_id = lawyer_id
        consultation.lawyer_result = result
        consultation.decision = decision
        await self.db.flush()
        return consultation

    def _sync_case_from_consultation(
        self,
        case,
        consultation_status: ConsultationStatus | str,
    ) -> None:
        status = ConsultationStatus(consultation_status)
        try:
            case_status, next_action = self._CASE_STATE_BY_CONSULTATION[status]
        except KeyError as exc:
            raise ActiveConsultationConflictError(
                f"Статус консультации {status.value} не относится к "
                "pre-payment этапу М2."
            ) from exc
        case.route = RouteCode.M2.value
        case.status = case_status.value
        case.next_action = next_action

    @staticmethod
    def _ensure_consultation_belongs_to_case(consultation, case) -> None:
        if consultation is None:
            raise ConsultationNotFoundError("Консультация не найдена.")
        if case is None or consultation.case_id != case.id:
            raise ConsultationNotFoundError(
                "Консультация не принадлежит указанному делу."
            )

    async def _add_history(
        self,
        *,
        case,
        consultation,
        action: str,
        actor_type: str,
        actor_id: int | None,
        source: str,
        details: dict | None = None,
    ) -> None:
        new_value = {
            "consultation_id": consultation.id,
            "consultation_status": str(consultation.status),
            "case_status": str(case.status),
            "source": source,
        }
        if details:
            new_value.update(details)
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action=action,
            new_value=new_value,
        )

    @classmethod
    def _is_current_hold(cls, slot, consultation, client_id: int) -> bool:
        return bool(
            slot
            and slot.status == "held"
            and slot.consultation_id == consultation.id
            and slot.held_by_user_id == client_id
            and slot.hold_expires_at
            and cls._as_utc(slot.hold_expires_at) > datetime.now(timezone.utc)
        )

    @classmethod
    def _is_future(cls, value: datetime) -> bool:
        return cls._as_utc(value) > datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
