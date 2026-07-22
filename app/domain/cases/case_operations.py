from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_timeline import (
    get_client_status_description,
    get_client_status_owner,
    get_client_visible_status,
    get_route_title,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.models.audit_log import AuditLog
from app.models.case import Case


@dataclass(frozen=True)
class SlaPolicy:
    hours: int
    owner: str
    objective: str


SLA_POLICIES: dict[str, SlaPolicy] = {
    CaseStatus.NEW.value: SlaPolicy(
        hours=4,
        owner="Менеджер",
        objective="Принять обращение и определить дальнейший маршрут.",
    ),
    CaseStatus.M1_DOCUMENTS_RECEIVED.value: SlaPolicy(
        hours=8,
        owner="Менеджер",
        objective="Проверить комплектность и передать материалы юристу.",
    ),
    CaseStatus.M1_LAWYER_REVIEW.value: SlaPolicy(
        hours=48,
        owner="Юрист",
        objective="Проверить документы и зафиксировать решение по ведению дела.",
    ),
    CaseStatus.M1_ACCEPTED.value: SlaPolicy(
        hours=24,
        owner="Юрист",
        objective="Подготовить договорные документы и следующий шаг клиента.",
    ),
    CaseStatus.M1_PAYMENT_30000_RECEIVED.value: SlaPolicy(
        hours=2,
        owner="Система / менеджер",
        objective="Открыть этап доверенности и проверить автоматический переход.",
    ),
    CaseStatus.M1_POA_RECEIVED.value: SlaPolicy(
        hours=24,
        owner="Юрист",
        objective="Начать подготовку претензии после получения доверенности.",
    ),
    CaseStatus.M1_CLAIM_PREPARATION.value: SlaPolicy(
        hours=72,
        owner="Юрист",
        objective="Подготовить претензию, требования и комплект приложений.",
    ),
    CaseStatus.M1_COURT_STAGE.value: SlaPolicy(
        hours=120,
        owner="Юрист",
        objective="Определить судебную стратегию и подготовить процессуальные документы.",
    ),
    CaseStatus.M1_PAYMENT_70000_RECEIVED.value: SlaPolicy(
        hours=2,
        owner="Система / менеджер",
        objective="Открыть оплаченный судебный этап без ручной задержки.",
    ),
    CaseStatus.M1_ENFORCEMENT.value: SlaPolicy(
        hours=168,
        owner="Юрист",
        objective="Обновить статус исполнения и следующий контрольный шаг.",
    ),
    CaseStatus.M1_MONEY_RECEIVED.value: SlaPolicy(
        hours=24,
        owner="Менеджер",
        objective="Зафиксировать результат и выставить итоговое вознаграждение.",
    ),
    CaseStatus.M1_SUCCESS_FEE_RECEIVED.value: SlaPolicy(
        hours=8,
        owner="Менеджер",
        objective="Проверить закрывающие действия и завершить дело.",
    ),
    CaseStatus.M2_CONSULTATION_DONE.value: SlaPolicy(
        hours=24,
        owner="Юрист",
        objective="Зафиксировать итог консультации и предложенный план действий.",
    ),
    CaseStatus.ERROR.value: SlaPolicy(
        hours=4,
        owner="Менеджер / администратор",
        objective="Разобрать ошибку и безопасно восстановить движение дела.",
    ),
}

CLIENT_WAITING_STATUSES = frozenset(
    {
        CaseStatus.CALCULATOR_STARTED.value,
        CaseStatus.CALCULATED.value,
        CaseStatus.CLIENT_DECISION.value,
        CaseStatus.M1_DOCUMENTS_PENDING.value,
        CaseStatus.M1_DOCS_REQUESTED.value,
        CaseStatus.M1_CONTRACT_READY.value,
        CaseStatus.M1_WAITING_PAYMENT_30000.value,
        CaseStatus.M1_POWER_OF_ATTORNEY.value,
        CaseStatus.M1_WAITING_PAYMENT_70000.value,
        CaseStatus.M1_WAITING_SUCCESS_FEE.value,
        CaseStatus.M2_CONSULTATION_ROUTE.value,
        CaseStatus.M2_DESCRIPTION_PENDING.value,
        CaseStatus.M2_DOCUMENTS_OPTIONAL.value,
        CaseStatus.M2_SLOT_PENDING.value,
        CaseStatus.M2_PAYMENT_PENDING.value,
        CaseStatus.M2_TO_M1.value,
    }
)

CLOSED_STATUSES = frozenset(
    {
        CaseStatus.M1_CLOSED.value,
        CaseStatus.M1_REJECTED.value,
        CaseStatus.M2_CLOSED.value,
        CaseStatus.ARCHIVED.value,
    }
)

ACTION_TITLES = {
    "CASE_CREATED": "Обращение создано",
    "CASE_STATUS_CHANGED": "Статус дела изменён",
    "CASE_TRANSFERRED_TO_M1": "Выбрано полное ведение дела",
    "CASE_TRANSFERRED_TO_M2": "Выбран консультационный маршрут",
    "LAWYER_ASSIGNED": "Назначен ответственный юрист",
    "CALCULATION_COMPLETED": "Предварительный расчёт завершён",
    "DOCUMENT_UPLOADED": "Документ добавлен",
    "DOCUMENTS_SENT_TO_REVIEW": "Документы переданы на проверку",
    "PAYMENT_CREATED": "Сформирован платёж",
    "PAYMENT_PAID": "Оплата подтверждена",
    "PAYMENT_WEBHOOK_PROCESSED": "Платёж обработан системой",
    "PAYMENT_WEBHOOK_REQUIRES_MANUAL_REVIEW": "Платёж требует ручной проверки",
    "CONSULTATION_CREATED": "Консультация создана",
    "CONSULTATION_DESCRIPTION_SAVED": "Описание ситуации сохранено",
    "CONSULTATION_SLOT_RESERVED": "Время консультации выбрано",
    "CONSULTATION_CONFIRMED": "Юрист подтвердил консультацию",
    "CONSULTATION_DECLINED": "Юрист не подтвердил выбранное время",
    "CONSULTATION_COMPLETED": "Консультация проведена",
    "CLIENT_MESSAGE_CREATED": "Сообщение сотруднику отправлено",
}

CLIENT_VISIBLE_ACTIONS = frozenset(
    {
        "CASE_CREATED",
        "CASE_STATUS_CHANGED",
        "CASE_TRANSFERRED_TO_M1",
        "CASE_TRANSFERRED_TO_M2",
        "LAWYER_ASSIGNED",
        "CALCULATION_COMPLETED",
        "DOCUMENT_UPLOADED",
        "DOCUMENTS_SENT_TO_REVIEW",
        "PAYMENT_CREATED",
        "PAYMENT_PAID",
        "CONSULTATION_CREATED",
        "CONSULTATION_DESCRIPTION_SAVED",
        "CONSULTATION_SLOT_RESERVED",
        "CONSULTATION_CONFIRMED",
        "CONSULTATION_DECLINED",
        "CONSULTATION_COMPLETED",
        "CLIENT_MESSAGE_CREATED",
    }
)

ACTOR_TITLES = {
    "client": "Клиент",
    "lawyer": "Юрист",
    "operator": "Менеджер / оператор",
    "manager": "Менеджер",
    "admin": "Администратор",
    "system": "Система",
    "scheduler": "Регламентная проверка",
    "payment_provider": "Платёжный провайдер",
}


def _value(value) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    normalized = _utc(value)
    return normalized.isoformat() if normalized else None


def _status_from_event(event: AuditLog, key: str) -> str | None:
    payload = event.new_value if key == "new" else event.old_value
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    return _value(status) if status else None


def _event_title(event: AuditLog) -> str:
    if event.action == "CASE_STATUS_CHANGED":
        status = _status_from_event(event, "new")
        if status:
            return get_client_visible_status(status)
    return ACTION_TITLES.get(event.action, event.action.replace("_", " ").title())


def _client_event_description(event: AuditLog) -> str:
    if event.action == "CASE_STATUS_CHANGED":
        status = _status_from_event(event, "new")
        if status:
            return get_client_status_description(status)
    if event.action == "LAWYER_ASSIGNED":
        return "Дело передано ответственному юристу."
    if event.action == "PAYMENT_CREATED":
        return "Платёж появился в разделе «Оплаты»."
    if event.action == "PAYMENT_PAID":
        return "Поступление подтверждено, связанный этап дела обновляется автоматически."
    if event.action == "CONSULTATION_SLOT_RESERVED":
        return "Выбранное время временно закреплено до завершения оплаты."
    if event.action == "CONSULTATION_CONFIRMED":
        return "Назначенный юрист подтвердил дату и время консультации."
    if event.action == "CONSULTATION_DECLINED":
        return "Нужно выбрать другое время или связаться с менеджером."
    return ACTION_TITLES.get(event.action, "Событие сохранено в истории дела.")


def serialize_case_history_event(
    event: AuditLog,
    *,
    client_view: bool = False,
) -> dict:
    old_status = _status_from_event(event, "old")
    new_status = _status_from_event(event, "new")
    actor_title = ACTOR_TITLES.get(event.actor_type, event.actor_type or "Система")
    if not client_view and event.actor_id is not None:
        actor_title = f"{actor_title} #{event.actor_id}"

    return {
        "id": event.id,
        "action": event.action,
        "title": _event_title(event),
        "description": (
            _client_event_description(event)
            if client_view
            else event.comment or _client_event_description(event)
        ),
        "actor_type": event.actor_type,
        "actor_id": None if client_view else event.actor_id,
        "actor_title": "Сервис" if client_view else actor_title,
        "old_status": old_status,
        "old_status_title": get_client_visible_status(old_status) if old_status else None,
        "new_status": new_status,
        "new_status_title": get_client_visible_status(new_status) if new_status else None,
        "old_value": None if client_view else event.old_value,
        "new_value": None if client_view else event.new_value,
        "comment": None if client_view else event.comment,
        "created_at": _iso(event.created_at),
    }


async def load_case_history(
    db: AsyncSession,
    *,
    case_id: int,
    limit: int = 100,
    client_view: bool = False,
) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    query = (
        select(AuditLog)
        .where(AuditLog.entity_type == "case")
        .where(AuditLog.entity_id == case_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit if not client_view else min(limit * 3, 500))
    )
    events = list((await db.execute(query)).scalars().all())
    if client_view:
        events = [event for event in events if event.action in CLIENT_VISIBLE_ACTIONS][:limit]
    return [
        serialize_case_history_event(event, client_view=client_view)
        for event in events
    ]


async def load_status_started_at(
    db: AsyncSession,
    cases: Sequence[Case] | Iterable[Case],
) -> dict[int, datetime]:
    case_list = list(cases)
    if not case_list:
        return {}

    by_id = {case.id: case for case in case_list}
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == "case")
        .where(AuditLog.entity_id.in_(by_id))
        .where(AuditLog.action.in_(("CASE_CREATED", "CASE_STATUS_CHANGED")))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    )

    status_started: dict[int, datetime] = {}
    created_events: dict[int, datetime] = {}
    for event in result.scalars().all():
        case_id = event.entity_id
        if case_id not in by_id or event.created_at is None:
            continue
        if event.action == "CASE_CREATED":
            created_events.setdefault(case_id, event.created_at)
            continue
        if case_id in status_started:
            continue
        if _status_from_event(event, "new") == _value(by_id[case_id].status):
            status_started[case_id] = event.created_at

    return {
        case.id: _utc(
            status_started.get(case.id)
            or created_events.get(case.id)
            or case.created_at
        )
        for case in case_list
    }


def build_case_sla(
    case: Case,
    *,
    status_started_at: datetime | None,
    now: datetime | None = None,
) -> dict:
    status = _value(case.status)
    started_at = _utc(status_started_at or case.created_at)
    current_time = _utc(now) or datetime.now(timezone.utc)

    base = {
        "status_started_at": _iso(started_at),
        "status_title": get_client_visible_status(status),
        "status_description": get_client_status_description(status),
        "status_owner": get_client_status_owner(status),
        "route_title": get_route_title(case.route),
        "due_at": None,
        "remaining_hours": None,
        "overdue_hours": None,
        "is_overdue": False,
    }

    if status in CLOSED_STATUSES:
        return {
            **base,
            "state": "completed",
            "state_title": "Завершено",
            "owner": "Действий не требуется",
            "objective": "Маршрут завершён или архивирован.",
            "target_hours": None,
        }

    if status == CaseStatus.M1_WAITING_30_DAYS.value:
        due_at = started_at + timedelta(days=30)
        remaining = (due_at - current_time).total_seconds() / 3600
        return {
            **base,
            "state": "legal_wait" if remaining >= 0 else "legal_term_elapsed",
            "state_title": (
                "Идёт установленный срок"
                if remaining >= 0
                else "Установленный срок истёк"
            ),
            "owner": "Система / юрист",
            "objective": "Контролировать истечение срока ответа на претензию.",
            "target_hours": 720,
            "due_at": _iso(due_at),
            "remaining_hours": round(max(remaining, 0), 1),
            "overdue_hours": round(max(-remaining, 0), 1),
            "is_overdue": remaining < 0,
        }

    if status in CLIENT_WAITING_STATUSES:
        return {
            **base,
            "state": "waiting_client",
            "state_title": "Ожидаем действие клиента",
            "owner": "Клиент",
            "objective": get_client_status_description(status),
            "target_hours": None,
        }

    policy = SLA_POLICIES.get(status)
    if policy is None:
        return {
            **base,
            "state": "monitoring",
            "state_title": "На контроле",
            "owner": get_client_status_owner(status),
            "objective": get_client_status_description(status),
            "target_hours": None,
        }

    due_at = started_at + timedelta(hours=policy.hours)
    remaining = (due_at - current_time).total_seconds() / 3600
    warning_threshold = min(4.0, policy.hours * 0.25)
    state = "overdue" if remaining < 0 else "due_soon" if remaining <= warning_threshold else "on_track"
    state_title = {
        "overdue": "SLA просрочен",
        "due_soon": "Срок скоро истекает",
        "on_track": "В пределах SLA",
    }[state]

    return {
        **base,
        "state": state,
        "state_title": state_title,
        "owner": policy.owner,
        "objective": policy.objective,
        "target_hours": policy.hours,
        "due_at": _iso(due_at),
        "remaining_hours": round(max(remaining, 0), 1),
        "overdue_hours": round(max(-remaining, 0), 1),
        "is_overdue": remaining < 0,
    }


def build_case_operational_summary(
    case: Case,
    *,
    status_started_at: datetime | None,
    now: datetime | None = None,
) -> dict:
    return {
        "case_id": case.id,
        "case_number": case.case_number,
        "route": _value(case.route),
        "status": _value(case.status),
        "next_action": case.next_action,
        "assigned_lawyer_id": case.assigned_lawyer_id,
        "sla": build_case_sla(
            case,
            status_started_at=status_started_at,
            now=now,
        ),
    }
