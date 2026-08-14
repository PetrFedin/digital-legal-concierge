from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.system.settings_service import SettingsService


class SlotUnavailableError(RuntimeError):
    pass


class SlotService:
    # Compatibility fallback only. Real holds are read from the validated live
    # setting ``consultations.slot_hold_minutes`` for every new reservation.
    HOLD_MINUTES = 30
    TEST_SLOT_DURATION_MINUTES = 60
    TEST_SLOT_STEP_MINUTES = 90

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_hold_minutes(self) -> int:
        value = await SettingsService(self.db).get_value(
            "consultations.slot_hold_minutes"
        )
        minutes = int(value)
        if minutes < 5 or minutes > 24 * 60:
            # SettingsService prevents new invalid writes. Fail closed as well
            # for a historical/manual DB value rather than creating an absurd
            # reservation window.
            raise SlotUnavailableError(
                "Настройка удержания слота некорректна. Обратитесь к администратору."
            )
        return minutes

    @staticmethod
    def _bulk(statement):
        return statement.execution_options(synchronize_session="fetch")

    async def release_expired_holds(self) -> int:
        """Release stale slots and return both M2 state machines to slot choice."""

        now = datetime.now(timezone.utc)
        expired = (
            await self.db.execute(
                select(
                    ConsultationSlot.id,
                    ConsultationSlot.consultation_id,
                    Consultation.case_id,
                )
                .outerjoin(
                    Consultation,
                    Consultation.id == ConsultationSlot.consultation_id,
                )
                .where(
                    ConsultationSlot.status == "held",
                    ConsultationSlot.hold_expires_at.is_not(None),
                    ConsultationSlot.hold_expires_at < now,
                )
            )
        ).all()
        if not expired:
            return 0

        slot_ids = [row.id for row in expired]
        consultation_ids = [
            row.consultation_id
            for row in expired
            if row.consultation_id is not None
        ]
        case_ids = sorted(
            {
                int(row.case_id)
                for row in expired
                if row.case_id is not None
            }
        )

        if consultation_ids:
            await self.db.execute(
                self._bulk(
                    update(Consultation)
                    .where(
                        Consultation.id.in_(consultation_ids),
                        Consultation.status.in_(
                            [
                                ConsultationStatus.SLOT_RESERVED,
                                ConsultationStatus.PAYMENT_PENDING,
                            ]
                        ),
                    )
                    .values(
                        slot_id=None,
                        lawyer_id=None,
                        scheduled_at=None,
                        status=ConsultationStatus.SLOT_PENDING,
                    )
                )
            )

        await self.db.execute(
            self._bulk(
                update(ConsultationSlot)
                .where(ConsultationSlot.id.in_(slot_ids))
                .values(
                    status="available",
                    hold_expires_at=None,
                    held_by_user_id=None,
                    consultation_id=None,
                )
            )
        )

        if case_ids:
            cases = list(
                (
                    await self.db.execute(
                        select(Case)
                        .where(Case.id.in_(case_ids))
                        .with_for_update()
                    )
                ).scalars().all()
            )
            case_service = CaseService(self.db)
            for case in cases:
                if str(case.status) != CaseStatus.M2_PAYMENT_PENDING.value:
                    continue
                await case_service.change_status(
                    case=case,
                    next_status=CaseStatus.M2_SLOT_PENDING,
                    actor_type="system",
                    actor_id=None,
                    comment=(
                        "Резерв консультации истёк до подтверждения оплаты. "
                        "Клиенту снова доступен выбор времени."
                    ),
                )

        await self.db.flush()
        return len(slot_ids)

    async def get_available_slots(
        self,
        lawyer_id: int | None = None,
        limit: int = 30,
    ) -> list[ConsultationSlot]:
        await self.release_expired_holds()
        now = datetime.now(timezone.utc)
        conditions = [
            ConsultationSlot.status == "available",
            ConsultationSlot.starts_at > now,
        ]
        if lawyer_id is not None:
            conditions.append(ConsultationSlot.lawyer_id == lawyer_id)
        result = await self.db.execute(
            select(ConsultationSlot)
            .where(and_(*conditions))
            .order_by(ConsultationSlot.starts_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_slot(self, slot_id: int) -> ConsultationSlot | None:
        await self.release_expired_holds()
        return (
            await self.db.execute(
                select(ConsultationSlot).where(ConsultationSlot.id == slot_id)
            )
        ).scalars().first()

    async def get_slot_for_update(
        self,
        slot_id: int,
    ) -> ConsultationSlot | None:
        return (
            await self.db.execute(
                select(ConsultationSlot)
                .where(ConsultationSlot.id == slot_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def hold_slot(
        self,
        slot_id: int,
        user_id: int,
        consultation_id: int,
    ) -> ConsultationSlot:
        await self.release_expired_holds()
        now = datetime.now(timezone.utc)
        hold_minutes = await self.get_hold_minutes()
        hold_expires_at = now + timedelta(minutes=hold_minutes)
        result = await self.db.execute(
            self._bulk(
                update(ConsultationSlot)
                .where(
                    ConsultationSlot.id == slot_id,
                    ConsultationSlot.status == "available",
                    ConsultationSlot.starts_at > now,
                )
                .values(
                    status="held",
                    held_by_user_id=user_id,
                    consultation_id=consultation_id,
                    hold_expires_at=hold_expires_at,
                )
            )
        )
        if result.rowcount != 1:
            raise SlotUnavailableError(
                "Это время уже занято или уже началось. Выберите другой слот."
            )
        await self.db.flush()
        slot = await self.get_slot(slot_id)
        if not slot:
            raise SlotUnavailableError("Слот не найден.")
        return slot

    async def book_available_slot(
        self,
        *,
        slot_id: int,
        user_id: int,
        consultation_id: int,
    ) -> ConsultationSlot:
        await self.release_expired_holds()
        result = await self.db.execute(
            self._bulk(
                update(ConsultationSlot)
                .where(
                    ConsultationSlot.id == slot_id,
                    ConsultationSlot.status == "available",
                    ConsultationSlot.starts_at > datetime.now(timezone.utc),
                )
                .values(
                    status="booked",
                    held_by_user_id=user_id,
                    consultation_id=consultation_id,
                    hold_expires_at=None,
                )
            )
        )
        if result.rowcount != 1:
            raise SlotUnavailableError(
                "Это время уже занято или уже началось. Выберите другой слот."
            )
        await self.db.flush()
        slot = await self.get_slot(slot_id)
        if not slot:
            raise SlotUnavailableError("Новый слот не найден.")
        return slot

    async def confirm_booking(
        self,
        slot_id: int,
        consultation_id: int,
    ) -> ConsultationSlot:
        result = await self.db.execute(
            self._bulk(
                update(ConsultationSlot)
                .where(
                    ConsultationSlot.id == slot_id,
                    ConsultationSlot.consultation_id == consultation_id,
                    ConsultationSlot.status == "held",
                    ConsultationSlot.hold_expires_at >= datetime.now(timezone.utc),
                )
                .values(status="booked", hold_expires_at=None)
            )
        )
        if result.rowcount != 1:
            existing = await self.get_slot(slot_id)
            if (
                not existing
                or existing.consultation_id != consultation_id
                or existing.status != "booked"
            ):
                raise SlotUnavailableError("Резерв слота не найден или истёк.")
        await self.db.flush()
        slot = await self.get_slot(slot_id)
        if not slot:
            raise SlotUnavailableError("Слот не найден.")
        return slot

    async def release_slot(
        self,
        slot_id: int,
        consultation_id: int | None = None,
    ) -> None:
        conditions = [ConsultationSlot.id == slot_id]
        if consultation_id is not None:
            conditions.append(ConsultationSlot.consultation_id == consultation_id)
        await self.db.execute(
            self._bulk(
                update(ConsultationSlot)
                .where(*conditions)
                .values(
                    status="available",
                    hold_expires_at=None,
                    held_by_user_id=None,
                    consultation_id=None,
                )
            )
        )
        await self.db.flush()

    async def create_test_slots(
        self,
        *,
        lawyer_id: int,
        count: int = 2,
        duration_minutes: int | None = None,
    ) -> list[ConsultationSlot]:
        if count not in {1, 2}:
            raise ValueError("Для теста можно создать один или два слота")
        duration = duration_minutes or self.TEST_SLOT_DURATION_MINUTES
        if duration < 15 or duration > 180:
            raise ValueError(
                "Продолжительность слота должна быть от 15 до 180 минут"
            )

        now = datetime.now(timezone.utc)
        cursor = now + timedelta(minutes=30)
        cursor = cursor.replace(second=0, microsecond=0)
        remainder = cursor.minute % 15
        if remainder:
            cursor += timedelta(minutes=15 - remainder)

        created: list[ConsultationSlot] = []
        attempts = 0
        while len(created) < count and attempts < 96:
            starts_at = cursor
            ends_at = starts_at + timedelta(minutes=duration)
            overlap = (
                await self.db.execute(
                    select(ConsultationSlot.id).where(
                        ConsultationSlot.lawyer_id == lawyer_id,
                        ConsultationSlot.status.in_(
                            ["available", "held", "booked"]
                        ),
                        ConsultationSlot.starts_at < ends_at,
                        ConsultationSlot.ends_at > starts_at,
                    )
                )
            ).scalars().first()
            if overlap is None:
                slot = ConsultationSlot(
                    lawyer_id=lawyer_id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    status="available",
                    note="Тестовый слот из админки",
                )
                self.db.add(slot)
                await self.db.flush()
                created.append(slot)
            cursor += timedelta(minutes=self.TEST_SLOT_STEP_MINUTES)
            attempts += 1

        if len(created) != count:
            raise ValueError(
                "Не удалось подобрать свободное время без пересечений"
            )
        return created
