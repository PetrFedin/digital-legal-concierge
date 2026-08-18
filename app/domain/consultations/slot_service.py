from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.payment import Payment
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
            raise SlotUnavailableError(
                "Настройка удержания слота некорректна. Обратитесь к администратору."
            )
        return minutes

    @staticmethod
    def _bulk(statement):
        return statement.execution_options(synchronize_session="fetch")

    @staticmethod
    def _reservation_key(consultation_id: int, slot_id: int) -> str:
        # Keep the same stable key contract as PaymentService without importing
        # it here (PaymentService depends on SlotService).
        return f"consultation:{int(consultation_id)}:slot:{int(slot_id)}"

    async def release_expired_holds(self) -> int:
        """Release only holds that are still expired after row-lock revalidation.

        The first query is intentionally only a candidate scan. A payment
        webhook can confirm a hold between that scan and cleanup. We therefore
        follow the provider's leading Payment lock order for matching active
        links, then lock/re-read the candidate Slot rows and mutate only rows
        that are *still* ``held`` and expired. A stale cleanup snapshot can never
        turn a newly ``booked`` slot back into ``available``.
        """

        now = datetime.now(timezone.utc)
        candidates = (
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
                .order_by(ConsultationSlot.id.asc())
            )
        ).all()
        if not candidates:
            return 0

        candidate_by_slot = {int(row.id): row for row in candidates}
        reservation_by_slot: dict[int, str] = {}
        case_by_reservation: dict[str, int] = {}
        for row in candidates:
            if row.consultation_id is None or row.case_id is None:
                continue
            key = self._reservation_key(int(row.consultation_id), int(row.id))
            reservation_by_slot[int(row.id)] = key
            case_by_reservation[key] = int(row.case_id)

        # Match webhook lock ordering. If a successful webhook already won and
        # committed, its payment is no longer active; the later slot recheck will
        # also see ``booked`` and skip it. If cleanup wins this lock, the webhook
        # waits and later sees EXPIRED/review state instead of resurrecting it.
        payment_ids_by_case: dict[int, list[int]] = {}
        if case_by_reservation:
            payment_rows = list(
                (
                    await self.db.execute(
                        select(Payment)
                        .where(
                            Payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT,
                            Payment.reservation_key.in_(list(case_by_reservation)),
                            Payment.status.in_(
                                [
                                    PaymentStatus.PENDING,
                                    PaymentStatus.WAITING_CONFIRMATION,
                                ]
                            ),
                        )
                        .order_by(Payment.id.asc())
                        .with_for_update()
                    )
                ).scalars().all()
            )
            for payment in payment_rows:
                old_status = payment.status
                payment.status = PaymentStatus.EXPIRED
                case_id = int(payment.case_id)
                payment_ids_by_case.setdefault(case_id, []).append(int(payment.id))
                await add_case_history_event(
                    self.db,
                    actor_type="system",
                    actor_id=None,
                    case_id=case_id,
                    action="CONSULTATION_PAYMENT_LINK_EXPIRED",
                    old_value={
                        "payment_id": payment.id,
                        "status": old_status,
                        "reservation_key": payment.reservation_key,
                    },
                    new_value={
                        "payment_id": payment.id,
                        "status": payment.status,
                        "reason": "slot_hold_expired",
                    },
                    comment=(
                        "Платёжная ссылка относится к истёкшему резерву времени и больше "
                        "не может автоматически подтвердить консультацию."
                    ),
                )

        candidate_ids = sorted(candidate_by_slot)
        locked_slots = list(
            (
                await self.db.execute(
                    select(ConsultationSlot)
                    .where(
                        ConsultationSlot.id.in_(candidate_ids),
                        ConsultationSlot.status == "held",
                        ConsultationSlot.hold_expires_at.is_not(None),
                        ConsultationSlot.hold_expires_at < now,
                    )
                    .order_by(ConsultationSlot.id.asc())
                    .with_for_update()
                )
            ).scalars().all()
        )
        if not locked_slots:
            # Provider/no-payment booking won the race after the candidate scan.
            # Active stale links may already have been expired above, but no slot
            # or consultation is reopened from an obsolete snapshot.
            await self.db.flush()
            return 0

        actual_slot_ids = [int(slot.id) for slot in locked_slots]
        expired = [candidate_by_slot[slot_id] for slot_id in actual_slot_ids]
        consultation_ids = sorted(
            {
                int(row.consultation_id)
                for row in expired
                if row.consultation_id is not None
            }
        )
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

        # Keep the state predicate even though rows are locked. It documents and
        # enforces that a booked row is never a valid expiry target.
        await self.db.execute(
            self._bulk(
                update(ConsultationSlot)
                .where(
                    ConsultationSlot.id.in_(actual_slot_ids),
                    ConsultationSlot.status == "held",
                    ConsultationSlot.hold_expires_at.is_not(None),
                    ConsultationSlot.hold_expires_at < now,
                )
                .values(
                    status="available",
                    hold_expires_at=None,
                    held_by_user_id=None,
                    consultation_id=None,
                )
            )
        )

        cases_by_id: dict[int, Case] = {}
        if case_ids:
            cases = list(
                (
                    await self.db.execute(
                        select(Case)
                        .where(Case.id.in_(case_ids))
                        .order_by(Case.id.asc())
                        .with_for_update()
                    )
                ).scalars().all()
            )
            cases_by_id = {int(case.id): case for case in cases}
            case_service = CaseService(self.db)
            for case in cases:
                if str(case.status) == CaseStatus.M2_PAYMENT_PENDING.value:
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

        for row in expired:
            if row.case_id is None:
                continue
            case_id = int(row.case_id)
            case = cases_by_id.get(case_id)
            await add_case_history_event(
                self.db,
                actor_type="system",
                actor_id=None,
                case_id=case_id,
                action="CONSULTATION_SLOT_HOLD_EXPIRED",
                old_value={
                    "slot_id": int(row.id),
                    "consultation_id": (
                        int(row.consultation_id)
                        if row.consultation_id is not None
                        else None
                    ),
                    "reservation_key": reservation_by_slot.get(int(row.id)),
                },
                new_value={
                    "slot_status": "available",
                    "consultation_status": (
                        ConsultationStatus.SLOT_PENDING.value
                        if row.consultation_id is not None
                        else None
                    ),
                    "case_status": str(case.status) if case is not None else None,
                    "expired_payment_ids": payment_ids_by_case.get(case_id, []),
                },
                comment=(
                    "Истёк резерв времени консультации. Старый слот освобождён, "
                    "активная ссылка этого резерва закрыта, клиент вернулся к выбору времени."
                ),
            )

        await self.db.flush()
        return len(actual_slot_ids)

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
