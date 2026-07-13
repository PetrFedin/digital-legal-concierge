from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.task import Task


@dataclass(frozen=True)
class WorkflowRule:
    title: str
    description: str
    priority: str = "NORMAL"
    due_hours: int | None = None
    assign_to_case_lawyer: bool = True


STATUS_RULES: dict[str, tuple[WorkflowRule, ...]] = {
    "NEW": (
        WorkflowRule(
            title="Проверить новое обращение",
            description="Проверить данные клиента, определить маршрут и следующий шаг.",
            priority="HIGH",
            due_hours=4,
            assign_to_case_lawyer=False,
        ),
    ),
    "M1_DOCUMENTS_RECEIVED": (
        WorkflowRule(
            title="Проверить комплект документов",
            description="Проверить загруженные документы, комплектность и читаемость файлов.",
            priority="HIGH",
            due_hours=24,
        ),
    ),
    "M1_LAWYER_REVIEW": (
        WorkflowRule(
            title="Провести юридическую проверку дела",
            description="Оценить перспективы, риски, комплектность и принять решение по делу.",
            priority="HIGH",
            due_hours=48,
        ),
    ),
    "M1_ACCEPTED": (
        WorkflowRule(
            title="Подготовить договор",
            description="Подготовить договор и материалы для начала сопровождения.",
            priority="HIGH",
            due_hours=24,
        ),
    ),
    "M1_PAYMENT_30000_RECEIVED": (
        WorkflowRule(
            title="Подготовить доверенность и инструкции",
            description="Сформировать инструкции клиенту по доверенности и дальнейшим действиям.",
            due_hours=24,
        ),
    ),
    "M1_POA_RECEIVED": (
        WorkflowRule(
            title="Подготовить претензию",
            description="Подготовить претензию и комплект приложений к отправке.",
            priority="HIGH",
            due_hours=72,
        ),
    ),
    "M1_CLAIM_SENT": (
        WorkflowRule(
            title="Контролировать срок ответа на претензию",
            description="Проверить истечение срока ответа и подготовить решение о судебном этапе.",
            due_hours=720,
        ),
    ),
    "M1_COURT_STAGE": (
        WorkflowRule(
            title="Обновить судебный статус",
            description="Проверить движение дела, заседания, определения и следующий процессуальный шаг.",
            priority="HIGH",
            due_hours=168,
        ),
    ),
    "M1_ENFORCEMENT": (
        WorkflowRule(
            title="Контролировать исполнение решения",
            description="Проверить исполнительный документ, производство и поступление денежных средств.",
            due_hours=168,
        ),
    ),
    "M2_DESCRIPTION_PENDING": (
        WorkflowRule(
            title="Контролировать описание вопроса клиентом",
            description="Проверить, предоставил ли клиент описание ситуации для консультации.",
            due_hours=24,
            assign_to_case_lawyer=False,
        ),
    ),
    "M2_CONSULTATION_BOOKED": (
        WorkflowRule(
            title="Подготовиться к консультации",
            description="Изучить описание, связанное дело и документы клиента до консультации.",
            priority="HIGH",
            due_hours=12,
        ),
    ),
    "M2_CONSULTATION_DONE": (
        WorkflowRule(
            title="Зафиксировать результат консультации",
            description="Записать выводы, рекомендации и дальнейший маршрут клиента.",
            priority="HIGH",
            due_hours=4,
        ),
    ),
}


class WorkflowEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def apply_case_status(self, case: Case, status: str) -> list[Task]:
        created: list[Task] = []
        for rule in STATUS_RULES.get(str(status), ()):
            task = await self._ensure_task(case, status, rule)
            if task:
                created.append(task)
        return created

    async def assign_unassigned_tasks(self, case: Case) -> int:
        if not case.assigned_lawyer_id:
            return 0
        rows = (
            await self.db.execute(
                select(Task).where(
                    Task.case_id == case.id,
                    Task.assigned_lawyer_id.is_(None),
                    Task.status.in_(["OPEN", "IN_PROGRESS", "BLOCKED"]),
                )
            )
        ).scalars().all()
        for task in rows:
            task.assigned_lawyer_id = case.assigned_lawyer_id
        if rows:
            await self.db.flush()
        return len(rows)

    async def _ensure_task(self, case: Case, status: str, rule: WorkflowRule) -> Task | None:
        existing = (
            await self.db.execute(
                select(Task).where(
                    Task.case_id == case.id,
                    Task.title == rule.title,
                    Task.status.in_(["OPEN", "IN_PROGRESS", "BLOCKED"]),
                )
            )
        ).scalars().first()
        if existing:
            return None

        due_at = None
        if rule.due_hours is not None:
            due_at = datetime.now(timezone.utc) + timedelta(hours=rule.due_hours)

        task = Task(
            case_id=case.id,
            assigned_lawyer_id=(
                case.assigned_lawyer_id if rule.assign_to_case_lawyer else None
            ),
            created_by_admin_user_id=None,
            title=rule.title,
            description=f"{rule.description}\n\nАвтоматически создано для статуса: {status}",
            status="OPEN",
            priority=rule.priority,
            due_at=due_at,
        )
        self.db.add(task)
        await self.db.flush()
        return task
