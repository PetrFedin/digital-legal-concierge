from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.statuses.case_statuses import CLOSED_CASE_STATUSES
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.lawyer import Lawyer


class LawyerCapacityError(RuntimeError):
    """A lawyer has no safe capacity for another active case reservation."""


@dataclass(frozen=True, slots=True)
class LawyerCapacitySnapshot:
    lawyer_id: int
    workload_limit: int
    current_workload: int

    @property
    def available_capacity(self) -> int:
        return max(self.workload_limit - self.current_workload, 0)

    @property
    def is_available(self) -> bool:
        return self.available_capacity > 0


class LawyerCapacityService:
    """Count assigned cases and unassigned consultation reservations once.

    A held or paid consultation consumes operational capacity even before
    ``Case.assigned_lawyer_id`` is written. The union by ``case_id`` prevents a
    booked and assigned consultation from being counted twice.
    """

    CAPACITY_CONSULTATION_STATUSES = frozenset(
        {
            ConsultationStatus.SLOT_RESERVED.value,
            ConsultationStatus.PAYMENT_PENDING.value,
            ConsultationStatus.PAID_PENDING_CONFIRMATION.value,
            ConsultationStatus.CONFIRMED.value,
            ConsultationStatus.BOOKED.value,
        }
    )

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _occupancy_union(*, exclude_case_id: int | None = None):
        assigned = select(
            Case.assigned_lawyer_id.label("lawyer_id"),
            Case.id.label("case_id"),
        ).where(
            Case.assigned_lawyer_id.is_not(None),
            Case.status.notin_(CLOSED_CASE_STATUSES),
        )
        reserved = (
            select(
                Consultation.lawyer_id.label("lawyer_id"),
                Consultation.case_id.label("case_id"),
            )
            .join(Case, Case.id == Consultation.case_id)
            .where(
                Consultation.lawyer_id.is_not(None),
                Consultation.status.in_(
                    LawyerCapacityService.CAPACITY_CONSULTATION_STATUSES
                ),
                Case.status.notin_(CLOSED_CASE_STATUSES),
            )
        )
        if exclude_case_id is not None:
            assigned = assigned.where(Case.id != exclude_case_id)
            reserved = reserved.where(Consultation.case_id != exclude_case_id)
        return union(assigned, reserved)

    async def lock_active_lawyer(self, lawyer_id: int) -> Lawyer:
        lawyer = (
            await self.db.execute(
                select(Lawyer)
                .where(
                    Lawyer.id == lawyer_id,
                    Lawyer.is_active.is_(True),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if lawyer is None:
            raise LawyerCapacityError("Активный юрист не найден.")
        return lawyer

    async def current_workload(
        self,
        *,
        lawyer_id: int,
        exclude_case_id: int | None = None,
    ) -> int:
        occupancy = self._occupancy_union(
            exclude_case_id=exclude_case_id
        ).subquery()
        return int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(occupancy)
                    .where(occupancy.c.lawyer_id == lawyer_id)
                )
            ).scalar_one()
            or 0
        )

    async def ensure_available(
        self,
        *,
        lawyer_id: int,
        exclude_case_id: int | None = None,
    ) -> LawyerCapacitySnapshot:
        lawyer = await self.lock_active_lawyer(lawyer_id)
        workload_limit = max(int(lawyer.workload_limit or 0), 0)
        current_workload = await self.current_workload(
            lawyer_id=lawyer_id,
            exclude_case_id=exclude_case_id,
        )
        snapshot = LawyerCapacitySnapshot(
            lawyer_id=lawyer.id,
            workload_limit=workload_limit,
            current_workload=current_workload,
        )
        if not snapshot.is_available:
            raise LawyerCapacityError(
                "Достигнут лимит активных дел юриста "
                f"({current_workload}/{workload_limit})."
            )
        return snapshot

    async def list_active_lawyers(self) -> list[tuple[Lawyer, LawyerCapacitySnapshot]]:
        occupancy = self._occupancy_union().subquery()
        counts = (
            select(
                occupancy.c.lawyer_id.label("lawyer_id"),
                func.count().label("current_workload"),
            )
            .group_by(occupancy.c.lawyer_id)
            .subquery()
        )
        rows = list(
            (
                await self.db.execute(
                    select(Lawyer, func.coalesce(counts.c.current_workload, 0))
                    .outerjoin(counts, counts.c.lawyer_id == Lawyer.id)
                    .where(Lawyer.is_active.is_(True))
                    .order_by(
                        func.coalesce(counts.c.current_workload, 0).asc(),
                        Lawyer.full_name.asc(),
                        Lawyer.id.asc(),
                    )
                )
            ).all()
        )
        result: list[tuple[Lawyer, LawyerCapacitySnapshot]] = []
        for lawyer, current_workload in rows:
            result.append(
                (
                    lawyer,
                    LawyerCapacitySnapshot(
                        lawyer_id=lawyer.id,
                        workload_limit=max(int(lawyer.workload_limit or 0), 0),
                        current_workload=int(current_workload or 0),
                    ),
                )
            )
        return result
