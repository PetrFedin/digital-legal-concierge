from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.notifications.notification_engine import NotificationEngine
from app.models.case import Case
from app.models.payment import Payment
from app.models.consultation import Consultation

class SchedulerJobs:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)

    async def check_unpaid_payments(self) -> int:
        result = await self.db.execute(select(Payment).where(Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"])))
        count = 0
        for payment in result.scalars().all():
            case = await self.db.get(Case, payment.case_id)
            await self.notifications.emit(event_code="PAYMENT_REMINDER", case_id=payment.case_id, payload={"case_number": case.case_number if case else payment.case_id})
            count += 1
        return count

    async def release_unpaid_consultation_slots(self) -> int:
        deadline = datetime.now(timezone.utc) - timedelta(minutes=30)
        result = await self.db.execute(select(Consultation).where(Consultation.status == "PAYMENT_PENDING").where(Consultation.updated_at < deadline))
        count = 0
        for consultation in result.scalars().all():
            consultation.status = "SLOT_PENDING"
            consultation.scheduled_at = None
            count += 1
        await self.db.flush()
        return count

    async def check_claim_waiting_30_days(self) -> int:
        deadline = datetime.now(timezone.utc) - timedelta(days=30)
        result = await self.db.execute(select(Case).where(Case.status == "M1_WAITING_30_DAYS").where(Case.updated_at < deadline))
        count = 0
        for case in result.scalars().all():
            await self.notifications.emit(event_code="CLAIM_30_DAYS_EXPIRED", case_id=case.id, payload={"case_number": case.case_number})
            count += 1
        return count
