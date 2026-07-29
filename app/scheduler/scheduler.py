import asyncio

from app.db.session import AsyncSessionLocal
from app.domain.notifications.notification_sender import NotificationSender
from app.scheduler.jobs import SchedulerJobs


class AppScheduler:
    def __init__(self, interval_seconds: int = 3600):
        self.interval_seconds = interval_seconds

    async def run_once(self) -> dict:
        async with AsyncSessionLocal() as db:
            jobs = SchedulerJobs(db)
            result = {
                "payment_reminders": await jobs.check_unpaid_payments(),
                "released_slots": (
                    await jobs.release_unpaid_consultation_slots()
                ),
                "consultation_reminders": (
                    await jobs.check_consultation_reminders()
                ),
                "consultation_completion_overdue": (
                    await jobs.check_consultation_completion_overdue()
                ),
                "case_sla": await jobs.check_case_sla(),
                "security_cleanup": await jobs.cleanup_security_state(),
                "claim_deadlines": (
                    await jobs.check_claim_waiting_30_days()
                ),
            }
            result["sent_notifications"] = await NotificationSender(
                db
            ).send_pending()
            await db.commit()
            return result

    async def run_forever(self):
        while True:
            await self.run_once()
            await asyncio.sleep(self.interval_seconds)
