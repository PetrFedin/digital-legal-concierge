from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, or_, select

from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.notification import Notification
from app.models.payment import Payment


CLOSED_CASE_STATUSES = ("M1_CLOSED", "M2_CLOSED", "ARCHIVED")
PAYMENT_WAITING_STATUSES = ("PENDING", "WAITING_CONFIRMATION")
DOCUMENT_REVIEW_STATUSES = (
    "UPLOADED",
    "ON_REVIEW",
    "PENDING_REVIEW",
    "REVIEW_REQUIRED",
)
SLA_OVERDUE_STATUSES = ("FIRST_RESPONSE_OVERDUE", "ACTION_OVERDUE")
ACTIVE_CONSULTATION_STATUSES = ("BOOKED", "CONFIRMED")


class AdminDashboardService:
    def __init__(self, db):
        self.db = db

    async def _count(self, stmt) -> int:
        result = await self.db.execute(stmt)
        return int(result.scalar_one() or 0)

    async def build(self):
        now = datetime.now(timezone.utc)
        today_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
        tomorrow_start = today_start + timedelta(days=1)
        sent_since = now - timedelta(hours=24)

        total_cases = await self._count(select(func.count(Case.id)))
        new_cases = await self._count(
            select(func.count(Case.id)).where(Case.status == "NEW")
        )
        active_cases = await self._count(
            select(func.count(Case.id)).where(
                Case.status.notin_(CLOSED_CASE_STATUSES)
            )
        )
        closed_cases = await self._count(
            select(func.count(Case.id)).where(
                Case.status.in_(CLOSED_CASE_STATUSES)
            )
        )
        unassigned_cases = await self._count(
            select(func.count(Case.id))
            .where(Case.assigned_lawyer_id.is_(None))
            .where(Case.status.notin_(CLOSED_CASE_STATUSES))
        )
        overdue_cases = await self._count(
            select(func.count(Case.id)).where(
                Case.sla_status.in_(SLA_OVERDUE_STATUSES)
            )
        )

        total_payments = await self._count(select(func.count(Payment.id)))
        waiting_payment = await self._count(
            select(func.count(Payment.id)).where(
                Payment.status.in_(PAYMENT_WAITING_STATUSES)
            )
        )
        payment_reviews = await self._count(
            select(func.count(Payment.id)).where(Payment.status == "PAID_REVIEW")
        )

        total_documents = await self._count(select(func.count(Document.id)))
        documents_review = await self._count(
            select(func.count(Document.id)).where(
                Document.status.in_(DOCUMENT_REVIEW_STATUSES)
            )
        )

        consultations_booked = await self._count(
            select(func.count(Consultation.id)).where(
                Consultation.status.in_(ACTIVE_CONSULTATION_STATUSES)
            )
        )
        consultations_today = await self._count(
            select(func.count(Consultation.id))
            .where(Consultation.status.in_(ACTIVE_CONSULTATION_STATUSES))
            .where(Consultation.scheduled_at >= today_start)
            .where(Consultation.scheduled_at < tomorrow_start)
        )

        notification_pending = await self._count(
            select(func.count(Notification.id)).where(
                Notification.status == "PENDING"
            )
        )
        notification_retry = await self._count(
            select(func.count(Notification.id)).where(Notification.status == "RETRY")
        )
        notification_failed = await self._count(
            select(func.count(Notification.id)).where(Notification.status == "FAILED")
        )
        notification_due = await self._count(
            select(func.count(Notification.id))
            .where(Notification.status.in_(["PENDING", "RETRY"]))
            .where(
                or_(
                    Notification.next_attempt_at.is_(None),
                    Notification.next_attempt_at <= now,
                )
            )
        )
        notification_sent_recent = await self._count(
            select(func.count(Notification.id))
            .where(Notification.status == "SENT")
            .where(Notification.sent_at >= sent_since)
        )
        notification_attention = (
            notification_pending + notification_retry + notification_failed
        )

        return {
            "generated_at": now.isoformat(),
            "cases": {
                "total": total_cases,
                "new": new_cases,
                "active": active_cases,
                "closed": closed_cases,
            },
            "queue": {
                "unassigned": unassigned_cases,
                "documents_review": documents_review,
                "consultations_today": consultations_today,
                "overdue": overdue_cases,
                "telegram_delivery": notification_attention,
                # Compatibility aliases for already deployed UI versions.
                "documents_for_review": documents_review,
                "sla_overdue": overdue_cases,
            },
            "payments": {
                "total": total_payments,
                "waiting": waiting_payment,
                "reviews": payment_reviews,
            },
            "documents": {
                "total": total_documents,
                "for_review": documents_review,
            },
            "consultations": {
                "booked": consultations_booked,
                "today": consultations_today,
            },
            "notifications": {
                "attention": notification_attention,
                "pending": notification_pending,
                "retry": notification_retry,
                "failed": notification_failed,
                "due_now": notification_due,
                "sent_recent": notification_sent_recent,
                "workspace": "/admin/notification-delivery/ui",
            },
            # Backward-compatible fields for existing integrations and reports.
            "new_cases": new_cases,
            "active_cases": active_cases,
            "waiting_payment": waiting_payment,
            "payment_reviews": payment_reviews,
            "sla_overdue": overdue_cases,
            "consultations_booked": consultations_booked,
            "closed_cases": closed_cases,
        }
