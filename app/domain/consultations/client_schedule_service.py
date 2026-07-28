from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.user import User


class ClientConsultationConflictError(RuntimeError):
    """The client already has an active overlapping consultation."""


class ClientConsultationScheduleService:
    """Serialize and validate client consultation schedule mutations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def ensure_interval_available(
        self,
        *,
        client_id: int,
        starts_at: datetime,
        ends_at: datetime,
        exclude_consultation_id: int | None = None,
    ) -> None:
        start = self._as_utc(starts_at)
        end = self._as_utc(ends_at)
        if end <= start:
            raise ClientConsultationConflictError(
                "Некорректный интервал консультации."
            )

        # Every current Telegram booking/reschedule path locks the same User
        # row before checking. Concurrent attempts for one client therefore
        # serialize on PostgreSQL while remaining functionally testable on
        # SQLite, where SELECT FOR UPDATE is a no-op.
        user = (
            await self.db.execute(
                select(User)
                .where(
                    User.id == client_id,
                    User.is_blocked.is_(False),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if user is None:
            raise ClientConsultationConflictError(
                "Клиент недоступен для записи на консультацию."
            )

        now = datetime.now(timezone.utc)
        query = (
            select(ConsultationSlot.id)
            .join(
                Consultation,
                Consultation.id == ConsultationSlot.consultation_id,
            )
            .join(Case, Case.id == Consultation.case_id)
            .where(
                Case.client_id == client_id,
                Consultation.status.in_(
                    {
                        ConsultationStatus.SLOT_RESERVED.value,
                        ConsultationStatus.PAYMENT_PENDING.value,
                        ConsultationStatus.PAID_PENDING_CONFIRMATION.value,
                        ConsultationStatus.CONFIRMED.value,
                        ConsultationStatus.BOOKED.value,
                    }
                ),
                ConsultationSlot.starts_at < end,
                ConsultationSlot.ends_at > start,
                or_(
                    ConsultationSlot.status == "booked",
                    and_(
                        ConsultationSlot.status == "held",
                        ConsultationSlot.hold_expires_at.is_not(None),
                        ConsultationSlot.hold_expires_at > now,
                    ),
                ),
            )
            .limit(1)
        )
        if exclude_consultation_id is not None:
            query = query.where(
                Consultation.id != exclude_consultation_id
            )
        conflict = (await self.db.execute(query)).scalar_one_or_none()
        if conflict is not None:
            raise ClientConsultationConflictError(
                "На это время у вас уже есть другая активная консультация."
            )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
