from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.task import Task


OPEN_CASE_STATUSES = ["M1_CLOSED", "M2_CLOSED", "ARCHIVED"]
OPEN_TASK_STATUSES = ["OPEN", "IN_PROGRESS", "BLOCKED"]


class AdminDashboardService:
    def __init__(self, db):
        self.db = db

    async def _count(self, statement) -> int:
        result = await self.db.execute(statement)
        return int(result.scalar_one() or 0)

    async def build(self) -> dict:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = today_start + timedelta(days=1)
        next_week = now + timedelta(days=7)

        new_cases = await self._count(
            select(func.count(Case.id)).where(Case.status == "NEW", Case.is_archived.is_(False))
        )
        active_cases = await self._count(
            select(func.count(Case.id)).where(
                Case.status.notin_(OPEN_CASE_STATUSES),
                Case.is_archived.is_(False),
            )
        )
        cases_without_lawyer = await self._count(
            select(func.count(Case.id)).where(
                Case.assigned_lawyer_id.is_(None),
                Case.status.notin_(OPEN_CASE_STATUSES),
                Case.is_archived.is_(False),
            )
        )
        waiting_payment = await self._count(
            select(func.count(Payment.id)).where(
                Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"])
            )
        )
        consultations_today = await self._count(
            select(func.count(Consultation.id)).where(
                Consultation.scheduled_at >= today_start,
                Consultation.scheduled_at < next_day,
                Consultation.status.in_(["BOOKED", "CONFIRMED", "M2_CONSULTATION_BOOKED"]),
            )
        )
        consultations_next_7_days = await self._count(
            select(func.count(Consultation.id)).where(
                Consultation.scheduled_at >= now,
                Consultation.scheduled_at <= next_week,
                Consultation.status.in_(["BOOKED", "CONFIRMED", "M2_CONSULTATION_BOOKED"]),
            )
        )
        documents_on_review = await self._count(
            select(func.count(Document.id)).where(
                Document.status.in_(["UPLOADED", "PENDING", "ON_REVIEW"])
            )
        )
        documents_overdue = await self._count(
            select(func.count(Document.id)).where(
                Document.status.in_(["UPLOADED", "PENDING", "ON_REVIEW"]),
                Document.created_at < now - timedelta(hours=24),
            )
        )
        tasks_open = await self._count(
            select(func.count(Task.id)).where(Task.status.in_(OPEN_TASK_STATUSES))
        )
        tasks_overdue = await self._count(
            select(func.count(Task.id)).where(
                Task.status.in_(OPEN_TASK_STATUSES),
                Task.due_at.is_not(None),
                Task.due_at < now,
            )
        )
        tasks_critical = await self._count(
            select(func.count(Task.id)).where(
                Task.status.in_(OPEN_TASK_STATUSES),
                Task.priority == "CRITICAL",
            )
        )
        pending_notifications = await self._count(
            select(func.count(Notification.id)).where(
                Notification.status.in_(["PENDING", "SCHEDULED", "RETRY"]),
                Notification.is_sent.is_(False),
            )
        )
        failed_notifications = await self._count(
            select(func.count(Notification.id)).where(Notification.status == "FAILED")
        )
        active_lawyers = await self._count(
            select(func.count(Lawyer.id)).where(Lawyer.is_active.is_(True))
        )

        lawyer_load_rows = (
            await self.db.execute(
                select(
                    Lawyer.id,
                    Lawyer.full_name,
                    Lawyer.workload_limit,
                    func.count(Case.id).label("active_cases"),
                )
                .outerjoin(
                    Case,
                    (Case.assigned_lawyer_id == Lawyer.id)
                    & Case.is_archived.is_(False)
                    & Case.status.notin_(OPEN_CASE_STATUSES),
                )
                .where(Lawyer.is_active.is_(True))
                .group_by(Lawyer.id, Lawyer.full_name, Lawyer.workload_limit)
                .order_by(func.count(Case.id).desc())
            )
        ).all()

        lawyer_load = []
        for lawyer_id, full_name, workload_limit, active_count in lawyer_load_rows:
            limit = int(workload_limit or 0)
            count = int(active_count or 0)
            utilization = round((count / limit) * 100, 1) if limit else None
            lawyer_load.append(
                {
                    "lawyer_id": lawyer_id,
                    "full_name": full_name,
                    "active_cases": count,
                    "workload_limit": limit,
                    "utilization_percent": utilization,
                    "overloaded": bool(limit and count > limit),
                }
            )

        risk_score = (
            tasks_overdue * 3
            + cases_without_lawyer * 3
            + documents_overdue * 2
            + failed_notifications * 4
            + tasks_critical * 2
        )
        health = "green" if risk_score == 0 else "yellow" if risk_score < 10 else "red"

        return {
            "generated_at": now.isoformat(),
            "health": health,
            "risk_score": risk_score,
            "cases": {
                "new": new_cases,
                "active": active_cases,
                "without_lawyer": cases_without_lawyer,
            },
            "tasks": {
                "open": tasks_open,
                "overdue": tasks_overdue,
                "critical": tasks_critical,
            },
            "documents": {
                "on_review": documents_on_review,
                "overdue": documents_overdue,
            },
            "payments": {"waiting": waiting_payment},
            "consultations": {
                "today": consultations_today,
                "next_7_days": consultations_next_7_days,
            },
            "notifications": {
                "pending": pending_notifications,
                "failed": failed_notifications,
            },
            "lawyers": {
                "active": active_lawyers,
                "load": lawyer_load,
            },
            "attention_required": {
                "tasks_overdue": tasks_overdue,
                "cases_without_lawyer": cases_without_lawyer,
                "documents_overdue": documents_overdue,
                "failed_notifications": failed_notifications,
            },
        }
