from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer


MOSCOW = ZoneInfo("Europe/Moscow")


class ConsultationSlotSelectionError(RuntimeError):
    """Client slot choices cannot be safely built for the supplied context."""


@dataclass(frozen=True, slots=True)
class ClientLawyerOption:
    reference: int
    full_name: str
    specialization: str | None
    nearest_at: datetime
    consultation_format: str


@dataclass(frozen=True, slots=True)
class ClientSlotOption:
    reference: int
    lawyer_reference: int
    lawyer_name: str
    specialization: str | None
    starts_at: datetime
    ends_at: datetime
    duration_minutes: int
    consultation_format: str


@dataclass(frozen=True, slots=True)
class ClientDateOption:
    value: date
    option_count: int


@dataclass(frozen=True, slots=True)
class ConsultationSlotSelection:
    assigned_lawyer_reference: int | None
    lawyers: tuple[ClientLawyerOption, ...]
    dates: tuple[ClientDateOption, ...]
    slots: tuple[ClientSlotOption, ...]


class ConsultationSlotSelectionService:
    """Build a client-safe, capacity-aware view of persistent lawyer slots."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_selection(
        self,
        *,
        client_id: int,
        case_id: int,
        consultation_id: int,
        lawyer_reference: int | None = None,
        now: datetime | None = None,
        horizon_days: int = 14,
    ) -> ConsultationSlotSelection:
        current_time = self._as_utc(now or datetime.now(timezone.utc))
        horizon = current_time + timedelta(days=max(1, min(horizon_days, 60)))
        row = (
            await self.db.execute(
                select(Case, Consultation)
                .join(Consultation, Consultation.case_id == Case.id)
                .where(
                    Case.id == case_id,
                    Case.client_id == client_id,
                    Case.route == RouteCode.M2.value,
                    Case.status == CaseStatus.M2_SLOT_PENDING.value,
                    Consultation.id == consultation_id,
                    Consultation.status == ConsultationStatus.SLOT_PENDING.value,
                )
            )
        ).one_or_none()
        if row is None:
            raise ConsultationSlotSelectionError(
                "Консультация недоступна для выбора времени."
            )
        case, consultation = row

        capacities = await CaseAssignmentService(self.db).list_active_lawyers()
        capacity_by_id = {item["id"]: item for item in capacities}
        if case.assigned_lawyer_id is not None:
            if case.assigned_lawyer_id not in capacity_by_id:
                allowed_lawyer_ids: set[int] = set()
            else:
                allowed_lawyer_ids = {case.assigned_lawyer_id}
        else:
            allowed_lawyer_ids = {
                item["id"] for item in capacities if item["is_available"]
            }
        if lawyer_reference is not None:
            if lawyer_reference not in allowed_lawyer_ids:
                raise ConsultationSlotSelectionError(
                    "Выбранный юрист сейчас недоступен для этой консультации."
                )
            allowed_lawyer_ids = {lawyer_reference}

        if not allowed_lawyer_ids:
            return ConsultationSlotSelection(
                assigned_lawyer_reference=case.assigned_lawyer_id,
                lawyers=(),
                dates=(),
                slots=(),
            )

        rows = list(
            (
                await self.db.execute(
                    select(ConsultationSlot, Lawyer)
                    .join(Lawyer, Lawyer.id == ConsultationSlot.lawyer_id)
                    .where(
                        ConsultationSlot.lawyer_id.in_(allowed_lawyer_ids),
                        ConsultationSlot.status == "available",
                        ConsultationSlot.consultation_id.is_(None),
                        ConsultationSlot.starts_at > current_time,
                        ConsultationSlot.starts_at < horizon,
                        Lawyer.is_active.is_(True),
                    )
                    .order_by(
                        ConsultationSlot.starts_at.asc(),
                        ConsultationSlot.ends_at.asc(),
                        Lawyer.full_name.asc(),
                        ConsultationSlot.id.asc(),
                    )
                )
            ).all()
        )

        client_busy_intervals = list(
            (
                await self.db.execute(
                    select(
                        ConsultationSlot.starts_at,
                        ConsultationSlot.ends_at,
                    )
                    .join(
                        Consultation,
                        Consultation.id == ConsultationSlot.consultation_id,
                    )
                    .join(Case, Case.id == Consultation.case_id)
                    .where(
                        Case.client_id == client_id,
                        Consultation.id != consultation.id,
                        Consultation.status.in_(
                            {
                                ConsultationStatus.CONFIRMED.value,
                                ConsultationStatus.BOOKED.value,
                            }
                        ),
                        ConsultationSlot.status == "booked",
                        ConsultationSlot.starts_at < horizon,
                        ConsultationSlot.ends_at > current_time,
                    )
                )
            ).all()
        )

        consultation_format = {
            "online": "Онлайн-консультация",
            "offline": "Личная встреча",
        }.get(
            str(consultation.consultation_type or "online").lower(),
            "Юридическая консультация",
        )
        slot_views: list[ClientSlotOption] = []
        for slot, lawyer in rows:
            starts_at = self._as_utc(slot.starts_at)
            ends_at = self._as_utc(slot.ends_at)
            if any(
                starts_at < self._as_utc(busy_end)
                and ends_at > self._as_utc(busy_start)
                for busy_start, busy_end in client_busy_intervals
            ):
                continue
            slot_views.append(
                ClientSlotOption(
                    reference=slot.id,
                    lawyer_reference=lawyer.id,
                    lawyer_name=lawyer.full_name,
                    specialization=lawyer.specialization,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    duration_minutes=max(
                        1,
                        int((ends_at - starts_at).total_seconds() // 60),
                    ),
                    consultation_format=consultation_format,
                )
            )
        slots = tuple(slot_views)

        nearest_by_lawyer: dict[int, ClientSlotOption] = {}
        for option in slots:
            nearest_by_lawyer.setdefault(option.lawyer_reference, option)
        lawyers = tuple(
            ClientLawyerOption(
                reference=lawyer_id,
                full_name=option.lawyer_name,
                specialization=option.specialization,
                nearest_at=option.starts_at,
                consultation_format=option.consultation_format,
            )
            for lawyer_id, option in sorted(
                nearest_by_lawyer.items(),
                key=lambda item: (
                    item[1].starts_at,
                    item[1].lawyer_name.casefold(),
                    item[0],
                ),
            )
        )

        by_date: dict[date, int] = {}
        for option in slots:
            local_date = option.starts_at.astimezone(MOSCOW).date()
            by_date[local_date] = by_date.get(local_date, 0) + 1
        dates = tuple(
            ClientDateOption(value=value, option_count=count)
            for value, count in sorted(by_date.items())
        )
        return ConsultationSlotSelection(
            assigned_lawyer_reference=case.assigned_lawyer_id,
            lawyers=lawyers,
            dates=dates,
            slots=slots,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
