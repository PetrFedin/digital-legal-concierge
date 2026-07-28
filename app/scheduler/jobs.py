from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.consultations.slot_service import SlotService
from app.domain.notifications.notification_engine import NotificationEngine
from app.models.case import Case
from app.models.consultation_slot import ConsultationSlot
from app.models.payment import Payment


class SchedulerJobs:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)

    async def check_unpaid_payments(self) -> int:
        result = await self.db.execute(
            select(Payment).where(
                Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"])
            )
        )
        count = 0
        for payment in result.scalars().all():
            case = await self.db.get(Case, payment.case_id)
            await self.notifications.emit(
                event_code="PAYMENT_REMINDER",
                case_id=payment.case_id,
                user_id=case.client_id if case else None,
                payload={
                    "case_number": case.case_number if case else payment.case_id
                },
            )
            count += 1
        return count

    async def release_unpaid_consultation_slots(self) -> int:
        """Release expired holds through the canonical slot-domain operation."""
        now = datetime.now(timezone.utc)
        expired_count = int(
            await self.db.scalar(
                select(func.count(ConsultationSlot.id)).where(
                    ConsultationSlot.status == "held",
                    ConsultationSlot.hold_expires_at.is_not(None),
                    ConsultationSlot.hold_expires_at < now,
                )
            )
            or 0
        )
        await SlotService(self.db).release_expired_holds()
        return expired_count

    async def check_claim_waiting_30_days(self) -> int:
        deadline = datetime.now(timezone.utc) - timedelta(days=30)
        result = await self.db.execute(
            select(Case).where(
                Case.status == "M1_WAITING_30_DAYS",
                Case.updated_at < deadline,
            )
        )
        count = 0
        for case in result.scalars().all():
            await self.notifications.emit(
                event_code="CLAIM_30_DAYS_EXPIRED",
                case_id=case.id,
                user_id=case.client_id,
                payload={"case_number": case.case_number},
            )
            count += 1
        return count
