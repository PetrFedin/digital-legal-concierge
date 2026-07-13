from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notifications.notification_engine import NotificationEngine
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.task import Task


OPEN_TASK_STATUSES = ["OPEN", "IN_PROGRESS", "BLOCKED"]


class SchedulerJobs:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)

    async def run_all(self) -> dict[str, int]:
        """Run one complete automation cycle.

        Each job is idempotent because notifications receive deterministic
        deduplication suffixes. A failed delivery can be retried by the sender
        without creating another notification record.
        """
        result = {
            "payment_reminders": await self.check_unpaid_payments(),
            "released_slots": await self.release_unpaid_consultation_slots(),
            "claim_deadlines": await self.check_claim_waiting_30_days(),
            "consultation_reminders": await self.check_consultation_reminders(),
            "client_actions": await self.check_client_actions(),
            "task_due_soon": await self.check_tasks_due_soon(),
            "task_overdue": await self.check_overdue_tasks(),
            "task_escalations": await self.escalate_overdue_tasks(),
            "cases_without_lawyer": await self.check_cases_without_lawyer(),
            "document_review_overdue": await self.check_document_review_sla(),
        }
        await self.db.flush()
        return result

    async def check_unpaid_payments(self) -> int:
        now = datetime.now(timezone.utc)
        rows = (
            await self.db.execute(
                select(Payment).where(Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"]))
            )
        ).scalars().all()
        count = 0
        for payment in rows:
            case = await self.db.get(Case, payment.case_id)
            if not case:
                continue
            age = now - payment.created_at
            if age < timedelta(hours=24):
                continue
            overdue = age >= timedelta(hours=72)
            event = "PAYMENT_OVERDUE" if overdue else "PAYMENT_REMINDER"
            bucket = "overdue" if overdue else f"day-{age.days}"
            created = await self.notifications.emit(
                event_code=event,
                case_id=case.id,
                payload={
                    "case_number": case.case_number,
                    "amount": payment.amount,
                    "deadline": (payment.created_at + timedelta(hours=72)).strftime("%d.%m.%Y %H:%M"),
                },
                dedup_suffix=f"payment:{payment.id}:{bucket}",
                action_label="Перейти к оплате",
                action_callback=f"payment_open:{payment.id}",
            )
            count += len(created)
        return count

    async def release_unpaid_consultation_slots(self) -> int:
        deadline = datetime.now(timezone.utc) - timedelta(minutes=30)
        rows = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.status == "PAYMENT_PENDING")
                .where(Consultation.updated_at < deadline)
            )
        ).scalars().all()
        for consultation in rows:
            consultation.status = "SLOT_PENDING"
            consultation.scheduled_at = None
            await self.notifications.emit(
                event_code="CLIENT_ACTION_REQUIRED",
                case_id=consultation.case_id,
                payload={
                    "case_number": consultation.case_id,
                    "action": "повторно выбрать время консультации",
                    "deadline": "как можно скорее",
                },
                dedup_suffix=f"consultation:{consultation.id}:slot-released",
                action_label="Выбрать время",
                action_callback="consult_slot_open",
            )
        await self.db.flush()
        return len(rows)

    async def check_claim_waiting_30_days(self) -> int:
        deadline = datetime.now(timezone.utc) - timedelta(days=30)
        rows = (
            await self.db.execute(
                select(Case)
                .where(Case.status.in_(["M1_WAITING_30_DAYS", "M1_CLAIM_SENT"]))
                .where(Case.updated_at < deadline)
            )
        ).scalars().all()
        count = 0
        for case in rows:
            created = await self.notifications.emit(
                event_code="CLAIM_30_DAYS_EXPIRED",
                case_id=case.id,
                payload={"case_number": case.case_number},
                dedup_suffix=f"claim-30-days:{case.updated_at.date().isoformat()}",
            )
            count += len(created)
        return count

    async def check_consultation_reminders(self) -> int:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=25)
        rows = (
            await self.db.execute(
                select(Consultation).where(
                    Consultation.status.in_(["BOOKED", "CONFIRMED", "M2_CONSULTATION_BOOKED"]),
                    Consultation.scheduled_at.is_not(None),
                    Consultation.scheduled_at >= now - timedelta(minutes=10),
                    Consultation.scheduled_at <= horizon,
                )
            )
        ).scalars().all()
        count = 0
        for consultation in rows:
            case = await self.db.get(Case, consultation.case_id)
            if not case or not consultation.scheduled_at:
                continue
            remaining = consultation.scheduled_at - now
            event_code = None
            bucket = None
            if timedelta(hours=23) <= remaining <= timedelta(hours=25):
                event_code, bucket = "CONSULTATION_REMINDER_24H", "24h"
            elif timedelta(minutes=110) <= remaining <= timedelta(minutes=130):
                event_code, bucket = "CONSULTATION_REMINDER_2H", "2h"
            elif timedelta(minutes=20) <= remaining <= timedelta(minutes=40):
                event_code, bucket = "CONSULTATION_REMINDER_30M", "30m"
            elif timedelta(minutes=-5) <= remaining <= timedelta(minutes=5):
                event_code, bucket = "CONSULTATION_STARTING", "start"
            if not event_code:
                continue
            created = await self.notifications.emit(
                event_code=event_code,
                case_id=case.id,
                payload={
                    "case_number": case.case_number,
                    "date": consultation.scheduled_at.strftime("%d.%m.%Y %H:%M"),
                    "format": consultation.consultation_type,
                },
                dedup_suffix=f"consultation:{consultation.id}:{bucket}",
                action_label="Открыть консультацию",
                action_callback=f"consultation_open:{consultation.id}",
            )
            count += len(created)
        return count

    async def check_client_actions(self) -> int:
        now = datetime.now(timezone.utc)
        rows = (
            await self.db.execute(
                select(Case).where(
                    Case.is_archived.is_(False),
                    Case.status.in_(
                        [
                            "M1_DOCUMENTS_PENDING",
                            "M1_DOCS_REQUESTED",
                            "M1_CONTRACT_READY",
                            "M1_POWER_OF_ATTORNEY",
                            "M2_DESCRIPTION_PENDING",
                            "M2_SLOT_PENDING",
                        ]
                    ),
                )
            )
        ).scalars().all()
        action_map = {
            "M1_DOCUMENTS_PENDING": ("загрузить документы", "documents_open"),
            "M1_DOCS_REQUESTED": ("загрузить недостающие документы", "documents_open"),
            "M1_CONTRACT_READY": ("проверить и подписать договор", "contract_open"),
            "M1_POWER_OF_ATTORNEY": ("оформить доверенность", "poa_instruction"),
            "M2_DESCRIPTION_PENDING": ("описать вопрос для юриста", "consult_description_start"),
            "M2_SLOT_PENDING": ("выбрать дату и время консультации", "consult_slot_open"),
        }
        count = 0
        for case in rows:
            age = now - case.updated_at
            if age < timedelta(hours=24):
                continue
            action, callback = action_map[case.status]
            bucket = f"day-{min(age.days, 30)}"
            created = await self.notifications.emit(
                event_code="CLIENT_ACTION_REPEAT",
                case_id=case.id,
                payload={"case_number": case.case_number, "action": action},
                dedup_suffix=f"client-action:{case.status}:{bucket}",
                action_label="Выполнить действие",
                action_callback=callback,
            )
            count += len(created)
        return count

    async def check_tasks_due_soon(self) -> int:
        now = datetime.now(timezone.utc)
        rows = (
            await self.db.execute(
                select(Task).where(
                    Task.status.in_(OPEN_TASK_STATUSES),
                    Task.due_at.is_not(None),
                    Task.due_at >= now,
                    Task.due_at <= now + timedelta(hours=24),
                )
            )
        ).scalars().all()
        count = 0
        for task in rows:
            case = await self.db.get(Case, task.case_id) if task.case_id else None
            created = await self.notifications.emit(
                event_code="TASK_DUE_SOON",
                case_id=task.case_id,
                admin_user_id=await self._task_assignee_admin_user_id(task),
                payload={
                    "task_title": task.title,
                    "case_number": case.case_number if case else "без дела",
                    "deadline": task.due_at.strftime("%d.%m.%Y %H:%M") if task.due_at else "—",
                },
                dedup_suffix=f"task:{task.id}:due-soon",
                action_label="Открыть задачу",
                action_callback=f"task_open:{task.id}",
            )
            count += len(created)
        return count

    async def check_overdue_tasks(self) -> int:
        now = datetime.now(timezone.utc)
        rows = (
            await self.db.execute(
                select(Task).where(
                    Task.status.in_(OPEN_TASK_STATUSES),
                    Task.due_at.is_not(None),
                    Task.due_at < now,
                )
            )
        ).scalars().all()
        count = 0
        for task in rows:
            case = await self.db.get(Case, task.case_id) if task.case_id else None
            overdue = now - task.due_at
            created = await self.notifications.emit(
                event_code="TASK_OVERDUE",
                case_id=task.case_id,
                admin_user_id=await self._task_assignee_admin_user_id(task),
                payload={
                    "task_title": task.title,
                    "case_number": case.case_number if case else "без дела",
                    "overdue": self._format_timedelta(overdue),
                },
                dedup_suffix=f"task:{task.id}:overdue-day-{overdue.days}",
                action_label="Открыть задачу",
                action_callback=f"task_open:{task.id}",
            )
            count += len(created)
        return count

    async def escalate_overdue_tasks(self) -> int:
        now = datetime.now(timezone.utc)
        rows = (
            await self.db.execute(
                select(Task).where(
                    Task.status.in_(OPEN_TASK_STATUSES),
                    Task.due_at.is_not(None),
                    Task.due_at < now - timedelta(hours=4),
                )
            )
        ).scalars().all()
        count = 0
        for task in rows:
            case = await self.db.get(Case, task.case_id) if task.case_id else None
            lawyer = await self.db.get(Lawyer, task.assigned_lawyer_id) if task.assigned_lawyer_id else None
            overdue = now - task.due_at
            created = await self.notifications.emit(
                event_code="TASK_ESCALATED",
                case_id=task.case_id,
                payload={
                    "task_title": task.title,
                    "case_number": case.case_number if case else "без дела",
                    "lawyer_name": lawyer.full_name if lawyer else "не назначен",
                },
                dedup_suffix=f"task:{task.id}:escalation-day-{overdue.days}",
                action_label="Проверить просрочку",
                action_callback=f"task_open:{task.id}",
            )
            count += len(created)
        return count

    async def check_cases_without_lawyer(self) -> int:
        now = datetime.now(timezone.utc)
        rows = (
            await self.db.execute(
                select(Case).where(
                    Case.assigned_lawyer_id.is_(None),
                    Case.is_archived.is_(False),
                    Case.status.notin_(["M1_CLOSED", "M2_CLOSED", "ARCHIVED"]),
                    Case.created_at < now - timedelta(hours=2),
                )
            )
        ).scalars().all()
        count = 0
        for case in rows:
            elapsed = now - case.created_at
            created = await self.notifications.emit(
                event_code="CASE_WITHOUT_LAWYER",
                case_id=case.id,
                payload={
                    "case_number": case.case_number,
                    "elapsed": self._format_timedelta(elapsed),
                },
                dedup_suffix=f"unassigned:day-{elapsed.days}:hour-{elapsed.seconds // 3600}",
                action_label="Назначить юриста",
                action_callback=f"case_assign:{case.id}",
            )
            count += len(created)
        return count

    async def check_document_review_sla(self) -> int:
        now = datetime.now(timezone.utc)
        rows = (
            await self.db.execute(
                select(Document).where(
                    Document.status.in_(["UPLOADED", "PENDING", "ON_REVIEW"]),
                    Document.created_at < now - timedelta(hours=24),
                )
            )
        ).scalars().all()
        count = 0
        for document in rows:
            case = await self.db.get(Case, document.case_id)
            lawyer = await self.db.get(Lawyer, case.assigned_lawyer_id) if case and case.assigned_lawyer_id else None
            age = now - document.created_at
            created = await self.notifications.emit(
                event_code="DOCUMENT_REVIEW_OVERDUE",
                case_id=document.case_id,
                payload={
                    "case_number": case.case_number if case else document.case_id,
                    "lawyer_name": lawyer.full_name if lawyer else "не назначен",
                },
                dedup_suffix=f"document:{document.id}:review-day-{age.days}",
                action_label="Проверить документ",
                action_callback=f"document_open:{document.id}",
            )
            count += len(created)
        return count

    async def _task_assignee_admin_user_id(self, task: Task) -> int | None:
        if not task.assigned_lawyer_id:
            return None
        lawyer = await self.db.get(Lawyer, task.assigned_lawyer_id)
        return lawyer.admin_user_id if lawyer else None

    @staticmethod
    def _format_timedelta(value: timedelta) -> str:
        days = value.days
        hours = value.seconds // 3600
        if days:
            return f"{days} дн. {hours} ч."
        return f"{hours} ч."
