from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.message import Message
from app.models.notification import Notification
from app.models.task import Task


OPEN_TASK_STATUSES = ["OPEN", "IN_PROGRESS", "BLOCKED"]
CLOSED_CASE_STATUSES = ["M1_CLOSED", "M2_CLOSED", "ARCHIVED"]


class LawyerDashboardService:
    def __init__(self, db, *, lawyer_id: int | None, all_access: bool = False):
        self.db = db
        self.lawyer_id = lawyer_id
        self.all_access = all_access

    async def _count(self, statement) -> int:
        result = await self.db.execute(statement)
        return int(result.scalar_one() or 0)

    def _case_scope(self):
        conditions = [Case.is_archived.is_(False)]
        if not self.all_access:
            conditions.append(Case.assigned_lawyer_id == self.lawyer_id)
        return conditions

    def _task_scope(self):
        conditions = []
        if not self.all_access:
            conditions.append(Task.assigned_lawyer_id == self.lawyer_id)
        return conditions

    async def build(self) -> dict:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today_start + timedelta(days=1)
        next_week = now + timedelta(days=7)

        case_scope = self._case_scope()
        active_cases = await self._count(
            select(func.count(Case.id)).where(
                *case_scope,
                Case.status.notin_(CLOSED_CASE_STATUSES),
            )
        )
        cases_waiting_action = await self._count(
            select(func.count(Case.id)).where(
                *case_scope,
                Case.status.in_(
                    [
                        "M1_LAWYER_REVIEW",
                        "M1_DOCUMENTS_RECEIVED",
                        "M1_COURT_STAGE",
                        "M1_ENFORCEMENT",
                        "M2_CONSULTATION_DONE",
                    ]
                ),
            )
        )

        task_scope = self._task_scope()
        open_tasks = await self._count(
            select(func.count(Task.id)).where(*task_scope, Task.status.in_(OPEN_TASK_STATUSES))
        )
        overdue_tasks = await self._count(
            select(func.count(Task.id)).where(
                *task_scope,
                Task.status.in_(OPEN_TASK_STATUSES),
                Task.due_at.is_not(None),
                Task.due_at < now,
            )
        )
        due_today = await self._count(
            select(func.count(Task.id)).where(
                *task_scope,
                Task.status.in_(OPEN_TASK_STATUSES),
                Task.due_at >= today_start,
                Task.due_at < tomorrow,
            )
        )

        consultation_conditions = [
            Consultation.status.in_(["BOOKED", "CONFIRMED", "M2_CONSULTATION_BOOKED"])
        ]
        if not self.all_access:
            consultation_conditions.append(Consultation.lawyer_id == self.lawyer_id)
        consultations_today = await self._count(
            select(func.count(Consultation.id)).where(
                *consultation_conditions,
                Consultation.scheduled_at >= today_start,
                Consultation.scheduled_at < tomorrow,
            )
        )
        consultations_week = await self._count(
            select(func.count(Consultation.id)).where(
                *consultation_conditions,
                Consultation.scheduled_at >= now,
                Consultation.scheduled_at <= next_week,
            )
        )

        case_ids_query = select(Case.id).where(*case_scope)
        documents_pending = await self._count(
            select(func.count(Document.id)).where(
                Document.case_id.in_(case_ids_query),
                Document.status.in_(["UPLOADED", "PENDING", "ON_REVIEW"]),
            )
        )
        documents_overdue = await self._count(
            select(func.count(Document.id)).where(
                Document.case_id.in_(case_ids_query),
                Document.status.in_(["UPLOADED", "PENDING", "ON_REVIEW"]),
                Document.created_at < now - timedelta(hours=24),
            )
        )
        unread_messages = await self._count(
            select(func.count(Message.id)).where(
                Message.case_id.in_(case_ids_query),
                or_(Message.is_read.is_(False), Message.is_read.is_(None)),
            )
        )

        unread_notifications = 0
        if self.lawyer_id:
            unread_notifications = await self._count(
                select(func.count(Notification.id)).where(
                    Notification.recipient_type == "lawyer",
                    Notification.is_read.is_(False),
                )
            )

        tasks = (
            await self.db.execute(
                select(Task)
                .where(*task_scope, Task.status.in_(OPEN_TASK_STATUSES))
                .order_by(Task.due_at.asc(), Task.priority.desc(), Task.id.desc())
                .limit(20)
            )
        ).scalars().all()

        consultations = (
            await self.db.execute(
                select(Consultation)
                .where(*consultation_conditions, Consultation.scheduled_at >= now)
                .order_by(Consultation.scheduled_at.asc())
                .limit(20)
            )
        ).scalars().all()

        attention = {
            "overdue_tasks": overdue_tasks,
            "documents_overdue": documents_overdue,
            "cases_waiting_action": cases_waiting_action,
            "unread_messages": unread_messages,
        }
        risk_score = overdue_tasks * 3 + documents_overdue * 2 + cases_waiting_action * 2 + unread_messages

        return {
            "generated_at": now.isoformat(),
            "health": "green" if risk_score == 0 else "yellow" if risk_score < 8 else "red",
            "risk_score": risk_score,
            "summary": {
                "active_cases": active_cases,
                "cases_waiting_action": cases_waiting_action,
                "open_tasks": open_tasks,
                "tasks_due_today": due_today,
                "overdue_tasks": overdue_tasks,
                "consultations_today": consultations_today,
                "consultations_next_7_days": consultations_week,
                "documents_pending": documents_pending,
                "documents_overdue": documents_overdue,
                "unread_messages": unread_messages,
                "unread_notifications": unread_notifications,
            },
            "attention_required": attention,
            "tasks": [
                {
                    "id": task.id,
                    "case_id": task.case_id,
                    "title": task.title,
                    "status": task.status,
                    "priority": task.priority,
                    "due_at": task.due_at.isoformat() if task.due_at else None,
                }
                for task in tasks
            ],
            "consultations": [
                {
                    "id": item.id,
                    "case_id": item.case_id,
                    "status": item.status,
                    "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
                    "subject_type": item.subject_type,
                    "client_description": item.client_description,
                }
                for item in consultations
            ],
        }
