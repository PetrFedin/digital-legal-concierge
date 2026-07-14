from __future__ import annotations

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.message import Message
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.task import Task


class CaseTimelineService:
    def __init__(self, db):
        self.db = db

    async def build(self, case_id: int, *, limit: int = 500) -> list[dict]:
        events: list[dict] = []

        audit_rows = (
            await self.db.execute(
                select(AuditLog)
                .where(AuditLog.entity_type == "case", AuditLog.entity_id == case_id)
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        for item in audit_rows:
            events.append(
                self._event(
                    event_type="audit",
                    title=self._audit_title(item.action),
                    occurred_at=item.created_at,
                    actor_type=item.actor_type,
                    actor_id=item.actor_id,
                    entity_type="case",
                    entity_id=case_id,
                    details={
                        "action": item.action,
                        "old_value": item.old_value,
                        "new_value": item.new_value,
                        "comment": item.comment,
                    },
                )
            )

        documents = (
            await self.db.execute(select(Document).where(Document.case_id == case_id))
        ).scalars().all()
        for item in documents:
            events.append(
                self._event(
                    event_type="document",
                    title=f"Документ: {item.title or item.document_type}",
                    occurred_at=item.created_at,
                    entity_type="document",
                    entity_id=item.id,
                    details={
                        "status": item.status,
                        "document_type": item.document_type,
                        "file_name": item.file_name,
                    },
                )
            )

        payments = (
            await self.db.execute(select(Payment).where(Payment.case_id == case_id))
        ).scalars().all()
        for item in payments:
            events.append(
                self._event(
                    event_type="payment",
                    title=f"Платеж: {item.amount} {item.currency}",
                    occurred_at=item.created_at,
                    entity_type="payment",
                    entity_id=item.id,
                    details={"status": item.status, "purpose": item.purpose},
                )
            )

        consultations = (
            await self.db.execute(select(Consultation).where(Consultation.case_id == case_id))
        ).scalars().all()
        for item in consultations:
            events.append(
                self._event(
                    event_type="consultation",
                    title="Консультация",
                    occurred_at=item.scheduled_at or item.created_at,
                    entity_type="consultation",
                    entity_id=item.id,
                    details={
                        "status": item.status,
                        "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
                        "subject_type": item.subject_type,
                        "client_description": item.client_description,
                    },
                )
            )

        messages = (
            await self.db.execute(select(Message).where(Message.case_id == case_id))
        ).scalars().all()
        for item in messages:
            events.append(
                self._event(
                    event_type="message",
                    title="Сообщение",
                    occurred_at=item.created_at,
                    actor_type=item.sender_type,
                    actor_id=item.sender_id,
                    entity_type="message",
                    entity_id=item.id,
                    details={"text": item.text, "is_read": item.is_read},
                )
            )

        tasks = (
            await self.db.execute(select(Task).where(Task.case_id == case_id))
        ).scalars().all()
        for item in tasks:
            events.append(
                self._event(
                    event_type="task",
                    title=f"Задача: {item.title}",
                    occurred_at=item.created_at,
                    entity_type="task",
                    entity_id=item.id,
                    details={
                        "status": item.status,
                        "priority": item.priority,
                        "due_at": item.due_at.isoformat() if item.due_at else None,
                        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
                    },
                )
            )

        notifications = (
            await self.db.execute(select(Notification).where(Notification.case_id == case_id))
        ).scalars().all()
        for item in notifications:
            events.append(
                self._event(
                    event_type="notification",
                    title=item.title or "Уведомление",
                    occurred_at=item.sent_at or item.scheduled_at or item.created_at,
                    entity_type="notification",
                    entity_id=item.id,
                    details={
                        "recipient_type": item.recipient_type,
                        "event_code": item.event_code,
                        "status": item.status,
                        "is_sent": item.is_sent,
                        "is_read": item.is_read,
                        "text": item.text,
                    },
                )
            )

        events.sort(key=lambda value: value["occurred_at"] or "", reverse=True)
        return events[:limit]

    @staticmethod
    def _event(
        *,
        event_type: str,
        title: str,
        occurred_at,
        actor_type: str | None = None,
        actor_id: int | None = None,
        entity_type: str | None = None,
        entity_id: int | None = None,
        details: dict | None = None,
    ) -> dict:
        return {
            "event_type": event_type,
            "title": title,
            "occurred_at": occurred_at.isoformat() if occurred_at else None,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": details or {},
        }

    @staticmethod
    def _audit_title(action: str) -> str:
        titles = {
            "CASE_CREATED": "Дело создано",
            "CASE_SELECTED": "Дело выбрано клиентом",
            "CASE_ARCHIVED": "Дело архивировано",
            "CASE_RESTORED": "Дело восстановлено",
            "CASE_STATUS_CHANGED": "Статус дела изменен",
            "LAWYER_ASSIGNED": "Назначен юрист",
            "WORKFLOW_TASKS_ASSIGNED": "Задачи назначены юристу",
            "CASE_TRANSFERRED_TO_M1": "Дело переведено в маршрут M1",
            "CASE_TRANSFERRED_TO_M2": "Дело переведено в маршрут M2",
        }
        return titles.get(action, action.replace("_", " ").title())
