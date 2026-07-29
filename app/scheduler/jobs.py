from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.sla_service import CaseSLAService
from app.domain.consultations.slot_service import SlotService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.payment import Payment
from app.security.login_throttle import LoginThrottleService
from app.security.token_revocation import cleanup_revoked_tokens


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SchedulerJobs:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)

    async def check_unpaid_payments(self) -> int:
        now = datetime.now(timezone.utc)
        reminder_cutoff = now - timedelta(minutes=15)
        payments = (
            await self.db.execute(
                select(Payment).where(
                    Payment.status.in_(
                        [
                            PaymentStatus.PENDING,
                            PaymentStatus.WAITING_CONFIRMATION,
                        ]
                    ),
                    Payment.created_at <= reminder_cutoff,
                )
            )
        ).scalars().all()

        count = 0
        day_key = now.date().isoformat()
        for payment in payments:
            case = await self.db.get(Case, payment.case_id)
            created = await self.notifications.emit(
                event_code="PAYMENT_REMINDER",
                case_id=payment.case_id,
                payload={
                    "case_number": case.case_number if case else payment.case_id
                },
                dedupe_key=f"payment:{payment.id}:reminder:{day_key}",
            )
            if created:
                count += 1
        return count

    async def release_unpaid_consultation_slots(self) -> int:
        return await SlotService(self.db).release_expired_holds()

    async def check_consultation_reminders(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=24)
        consultations = (
            await self.db.execute(
                select(Consultation)
                .where(
                    Consultation.status == ConsultationStatus.BOOKED,
                    Consultation.scheduled_at.is_not(None),
                    Consultation.scheduled_at > now,
                    Consultation.scheduled_at <= horizon,
                )
                .order_by(Consultation.scheduled_at.asc())
            )
        ).scalars().all()

        result = {"within_24h": 0, "within_2h": 0}
        for consultation in consultations:
            scheduled_at = as_utc(consultation.scheduled_at)
            remaining = scheduled_at - now
            case = await self.db.get(Case, consultation.case_id)
            payload = {
                "case_number": (
                    case.case_number if case else consultation.case_id
                ),
                "date": scheduled_at.strftime("%d.%m.%Y %H:%M UTC"),
            }
            if remaining <= timedelta(hours=2):
                event_code = "CONSULTATION_REMINDER_2H"
                bucket = "within_2h"
                key_suffix = "2h"
            else:
                event_code = "CONSULTATION_REMINDER_24H"
                bucket = "within_24h"
                key_suffix = "24h"

            created = await self.notifications.emit(
                event_code=event_code,
                case_id=consultation.case_id,
                payload=payload,
                dedupe_key=(
                    f"consultation:{consultation.id}:reminder:{key_suffix}"
                ),
            )
            if created:
                result[bucket] += 1
        return result

    async def check_consultation_completion_overdue(self) -> int:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=15)
        rows = (
            await self.db.execute(
                select(Consultation, ConsultationSlot)
                .join(
                    ConsultationSlot,
                    ConsultationSlot.id == Consultation.slot_id,
                )
                .where(
                    Consultation.status == ConsultationStatus.BOOKED,
                    ConsultationSlot.status == "booked",
                    ConsultationSlot.ends_at <= cutoff,
                )
                .order_by(ConsultationSlot.ends_at.asc())
            )
        ).all()

        count = 0
        for consultation, slot in rows:
            case = await self.db.get(Case, consultation.case_id)
            created = await self.notifications.emit(
                event_code="CONSULTATION_COMPLETION_OVERDUE",
                case_id=consultation.case_id,
                payload={
                    "case_number": (
                        case.case_number if case else consultation.case_id
                    ),
                    "date": as_utc(slot.starts_at).strftime(
                        "%d.%m.%Y %H:%M UTC"
                    ),
                },
                dedupe_key=(
                    f"consultation:{consultation.id}:completion-overdue"
                ),
            )
            if created:
                count += 1
        return count

    async def check_case_sla(self) -> dict:
        return await CaseSLAService(self.db).escalate_overdue_cases()

    async def cleanup_security_state(self) -> dict[str, int]:
        return {
            "login_states": await LoginThrottleService(self.db).cleanup(),
            "revoked_tokens": await cleanup_revoked_tokens(self.db),
        }

    async def check_claim_waiting_30_days(self) -> int:
        deadline = datetime.now(timezone.utc) - timedelta(days=30)
        cases = (
            await self.db.execute(
                select(Case)
                .where(Case.status == "M1_WAITING_30_DAYS")
                .where(Case.updated_at < deadline)
            )
        ).scalars().all()

        count = 0
        for case in cases:
            created = await self.notifications.emit(
                event_code="CLAIM_30_DAYS_EXPIRED",
                case_id=case.id,
                payload={"case_number": case.case_number},
                dedupe_key=f"case:{case.id}:claim-30-days-expired",
            )
            if created:
                count += 1
        return count
