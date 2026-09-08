from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_timeline import get_client_visible_status
from app.models.audit_log import AuditLog
from app.presentation_time import format_business_datetime

ActivityAudience = Literal["client", "staff"]

CLIENT_VISIBLE_ACTIONS = frozenset(
    {
        "CASE_CREATED",
        "CALCULATION_COMPLETED",
        "CASE_STATUS_CHANGED",
        "CASE_TRANSFERRED_TO_M1",
        "CASE_TRANSFERRED_TO_M2",
        "LAWYER_ASSIGNED",
        "DOCUMENT_UPLOADED",
        "DOCUMENTS_SENT_TO_REVIEW",
        "DOCUMENT_REVIEW_DECISION",
        "CONSULTATION_CREATED",
        "CONSULTATION_DESCRIPTION_SAVED",
        "CONSULTATION_SLOT_HELD",
        "CONSULTATION_SLOT_RESERVED",
        "CONSULTATION_BOOKED_AFTER_PAYMENT",
        "CONSULTATION_BOOKED_WITHOUT_PAYMENT",
        "CONSULTATION_RESCHEDULED",
        "CONSULTATION_CANCELLED",
        "CONSULTATION_DONE",
        "CLIENT_MESSAGE_CREATED",
        "LAWYER_MESSAGE_CREATED",
        "PAYMENT_CREATED",
        "PAYMENT_PAID",
        "PAYMENT_CONFIRMED",
        "PAYMENT_REFUND_REQUESTED",
        "CONTRACT_READY",
        "CONTRACT_SIGNED",
        "POWER_OF_ATTORNEY_RECEIVED",
        "CLAIM_PREPARED",
        "CLAIM_SENT",
        "COURT_EVENT_ADDED",
        "ENFORCEMENT_STARTED",
        "CASE_CLOSED",
    }
)

STAFF_ONLY_ACTIONS = frozenset(
    {
        "SLA_STARTED",
        "SLA_FIRST_RESPONSE_RECORDED",
        "SLA_ACTION_RECORDED",
        "SLA_OVERDUE",
        "SLA_ACKNOWLEDGED",
        "CASE_AUTO_ASSIGNED",
        "DOCUMENT_UPLOAD_REJECTED",
        "DOCUMENT_PENDING_VERSION_SUPERSEDED",
        "DOCUMENT_PREVIOUS_VERSIONS_ARCHIVED",
        "CONSULTATION_LAWYER_NO_SHOW",
        "CONSULTATION_REFUND_QUEUED",
    }
)

TITLE_BY_ACTION = {
    "CASE_CREATED": "Обращение создано",
    "CALCULATION_COMPLETED": "Предварительный расчёт готов",
    "CASE_STATUS_CHANGED": "Этап дела обновлён",
    "CASE_TRANSFERRED_TO_M1": "Начато ведение дела",
    "CASE_TRANSFERRED_TO_M2": "Выбрана консультация",
    "LAWYER_ASSIGNED": "Назначен ответственный юрист",
    "CASE_AUTO_ASSIGNED": "Юрист назначен автоматически",
    "DOCUMENT_UPLOADED": "Документ загружен",
    "DOCUMENTS_SENT_TO_REVIEW": "Документы переданы юристу",
    "DOCUMENT_REVIEW_DECISION": "Юрист проверил документ",
    "DOCUMENT_UPLOAD_REJECTED": "Файл не прошёл безопасную загрузку",
    "DOCUMENT_PENDING_VERSION_SUPERSEDED": "Загружена новая версия документа",
    "DOCUMENT_PREVIOUS_VERSIONS_ARCHIVED": "Предыдущие версии сохранены в истории",
    "CONSULTATION_CREATED": "Создано обращение на консультацию",
    "CONSULTATION_DESCRIPTION_SAVED": "Описание ситуации сохранено",
    "CONSULTATION_SLOT_HELD": "Время консультации временно зарезервировано",
    "CONSULTATION_SLOT_RESERVED": "Время консультации выбрано",
    "CONSULTATION_BOOKED_AFTER_PAYMENT": "Консультация подтверждена",
    "CONSULTATION_BOOKED_WITHOUT_PAYMENT": "Консультация подтверждена",
    "CONSULTATION_RESCHEDULED": "Консультация перенесена",
    "CONSULTATION_CANCELLED": "Консультация отменена",
    "CONSULTATION_DONE": "Консультация проведена",
    "CONSULTATION_LAWYER_NO_SHOW": "Зафиксирована неявка юриста",
    "CONSULTATION_REFUND_QUEUED": "Возврат оплаты поставлен в очередь",
    "CLIENT_MESSAGE_CREATED": "Вопрос юридической команде отправлен",
    "LAWYER_MESSAGE_CREATED": "Юридическая команда ответила",
    "PAYMENT_CREATED": "Создан платёж",
    "PAYMENT_PAID": "Оплата подтверждена",
    "PAYMENT_CONFIRMED": "Оплата подтверждена",
    "PAYMENT_REFUND_REQUESTED": "Запрошен возврат оплаты",
    "CONTRACT_READY": "Договор готов",
    "CONTRACT_SIGNED": "Договор подписан",
    "POWER_OF_ATTORNEY_RECEIVED": "Доверенность получена",
    "CLAIM_PREPARED": "Претензия подготовлена",
    "CLAIM_SENT": "Претензия направлена",
    "COURT_EVENT_ADDED": "Обновлён судебный этап",
    "ENFORCEMENT_STARTED": "Начато исполнительное производство",
    "CASE_CLOSED": "Дело завершено",
    "SLA_STARTED": "Запущен контроль срока реакции",
    "SLA_FIRST_RESPONSE_RECORDED": "Первая реакция зафиксирована",
    "SLA_ACTION_RECORDED": "Действие по SLA зафиксировано",
    "SLA_OVERDUE": "Зафиксирована просрочка SLA",
    "SLA_ACKNOWLEDGED": "Просрочка SLA принята в работу",
}

CATEGORY_BY_ACTION = {
    "CALCULATION_COMPLETED": "calculation",
    "DOCUMENT_UPLOADED": "documents",
    "DOCUMENTS_SENT_TO_REVIEW": "documents",
    "DOCUMENT_REVIEW_DECISION": "documents",
    "DOCUMENT_UPLOAD_REJECTED": "documents",
    "DOCUMENT_PENDING_VERSION_SUPERSEDED": "documents",
    "DOCUMENT_PREVIOUS_VERSIONS_ARCHIVED": "documents",
    "CONSULTATION_CREATED": "consultation",
    "CONSULTATION_DESCRIPTION_SAVED": "consultation",
    "CONSULTATION_SLOT_HELD": "consultation",
    "CONSULTATION_SLOT_RESERVED": "consultation",
    "CONSULTATION_BOOKED_AFTER_PAYMENT": "consultation",
    "CONSULTATION_BOOKED_WITHOUT_PAYMENT": "consultation",
    "CONSULTATION_RESCHEDULED": "consultation",
    "CONSULTATION_CANCELLED": "consultation",
    "CONSULTATION_DONE": "consultation",
    "CONSULTATION_LAWYER_NO_SHOW": "consultation",
    "CONSULTATION_REFUND_QUEUED": "payments",
    "CLIENT_MESSAGE_CREATED": "messages",
    "LAWYER_MESSAGE_CREATED": "messages",
    "PAYMENT_CREATED": "payments",
    "PAYMENT_PAID": "payments",
    "PAYMENT_CONFIRMED": "payments",
    "PAYMENT_REFUND_REQUESTED": "payments",
    "CONTRACT_READY": "contract",
    "CONTRACT_SIGNED": "contract",
    "POWER_OF_ATTORNEY_RECEIVED": "documents",
    "CLAIM_PREPARED": "legal_stage",
    "CLAIM_SENT": "legal_stage",
    "COURT_EVENT_ADDED": "legal_stage",
    "ENFORCEMENT_STARTED": "legal_stage",
    "SLA_STARTED": "sla",
    "SLA_FIRST_RESPONSE_RECORDED": "sla",
    "SLA_ACTION_RECORDED": "sla",
    "SLA_OVERDUE": "sla",
    "SLA_ACKNOWLEDGED": "sla",
}

DOCUMENT_TYPE_LABELS = {
    "DDU": "ДДУ",
    "APPENDIX": "Приложение",
    "ADDITIONAL_AGREEMENT": "Допсоглашение",
    "TRANSFER_ACT": "Акт приёма-передачи",
    "PAYMENT_PROOF": "Платёжный документ",
    "CORRESPONDENCE": "Переписка",
    "OTHER": "Документ",
}

DOCUMENT_STATUS_LABELS = {
    "APPROVED": "принят",
    "NEEDS_REUPLOAD": "нужна новая версия",
    "REJECTED": "отклонён",
    "ON_REVIEW": "передан на проверку",
}

ACTOR_LABELS = {
    "client": "Клиент",
    "lawyer": "Юрист",
    "admin": "Администратор",
    "admin_user": "Администратор",
    "system": "Система",
}


@dataclass(frozen=True, slots=True)
class CaseActivityItem:
    id: int
    occurred_at: str | None
    code: str
    title: str
    detail: str | None
    category: str
    actor_label: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _clean_text(value: object, limit: int = 220) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "…"


def _payload(log: AuditLog) -> dict[str, object]:
    return log.new_value if isinstance(log.new_value, dict) else {}


def _status_label(value: object) -> str | None:
    status = str(value or "").strip()
    if not status:
        return None
    return get_client_visible_status(status)


def _format_money(value: object) -> str | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except Exception:
        return None
    return f"{amount:,.2f}".replace(",", " ") + " ₽"


def _format_datetime(value: object) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return format_business_datetime(
        parsed,
        pattern="%d.%m.%Y в %H:%M",
    )


def _document_label(payload: dict[str, object]) -> str:
    raw = payload.get("document") or payload.get("title") or payload.get("type")
    if raw in DOCUMENT_TYPE_LABELS:
        return DOCUMENT_TYPE_LABELS[str(raw)]
    return _clean_text(raw, 80) or "Документ"


def _safe_detail(log: AuditLog, audience: ActivityAudience) -> str | None:
    action = str(log.action)
    payload = _payload(log)

    if action in {
        "CASE_STATUS_CHANGED",
        "CASE_TRANSFERRED_TO_M1",
        "CASE_TRANSFERRED_TO_M2",
    }:
        label = _status_label(payload.get("status"))
        return f"Текущий этап: {label}." if label else None

    if action == "CALCULATION_COMPLETED":
        amount = _format_money(
            payload.get("penalty_amount") or payload.get("amount")
        )
        return f"Предварительная сумма: {amount}." if amount else None

    if action == "DOCUMENT_UPLOADED":
        version = payload.get("version")
        suffix = f", версия {version}" if version else ""
        return f"{_document_label(payload)}{suffix}. Файл ещё не передан юристу."

    if action == "DOCUMENTS_SENT_TO_REVIEW":
        count = payload.get("new_count") or payload.get("count")
        if count is not None:
            return f"Передано новых файлов: {count}."
        return "Пакет передан юридической команде."

    if action == "DOCUMENT_REVIEW_DECISION":
        status = DOCUMENT_STATUS_LABELS.get(str(payload.get("status") or ""))
        detail = f"{_document_label(payload)}: {status}." if status else None
        comment = _clean_text(log.comment, 180)
        if comment:
            return f"{detail or _document_label(payload) + '.'} Комментарий: {comment}"
        return detail

    if action in {
        "CONSULTATION_SLOT_HELD",
        "CONSULTATION_SLOT_RESERVED",
        "CONSULTATION_BOOKED_AFTER_PAYMENT",
        "CONSULTATION_BOOKED_WITHOUT_PAYMENT",
    }:
        scheduled = _format_datetime(
            payload.get("scheduled_at") or payload.get("starts_at")
        )
        return f"Дата и время: {scheduled}." if scheduled else None

    if action == "CONSULTATION_RESCHEDULED":
        scheduled = _format_datetime(
            payload.get("starts_at") or payload.get("scheduled_at")
        )
        return f"Новое время: {scheduled}." if scheduled else None

    if action in {"PAYMENT_CREATED", "PAYMENT_PAID", "PAYMENT_CONFIRMED"}:
        amount = _format_money(payload.get("amount"))
        title = _clean_text(payload.get("title"), 100)
        parts = [part for part in (title, amount) if part]
        return " · ".join(parts) + ("." if parts else "") or None

    if action == "LAWYER_ASSIGNED":
        return "Ответственный появился в карточке дела."

    if audience == "staff" and action in STAFF_ONLY_ACTIONS:
        return _clean_text(log.comment, 220)

    return None


def present_case_activity(
    log: AuditLog,
    *,
    audience: ActivityAudience,
) -> CaseActivityItem | None:
    action = str(log.action)
    allowed = CLIENT_VISIBLE_ACTIONS | (
        STAFF_ONLY_ACTIONS if audience == "staff" else frozenset()
    )
    if action not in allowed:
        return None
    title = TITLE_BY_ACTION.get(action)
    if not title:
        return None
    occurred_at = log.created_at.isoformat() if log.created_at else None
    return CaseActivityItem(
        id=int(log.id),
        occurred_at=occurred_at,
        code=action,
        title=title,
        detail=_safe_detail(log, audience),
        category=CATEGORY_BY_ACTION.get(action, "case"),
        actor_label=ACTOR_LABELS.get(str(log.actor_type), "Команда"),
    )


class CaseActivityService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def page(
        self,
        *,
        case_id: int,
        audience: ActivityAudience,
        before_id: int | None = None,
        limit: int = 8,
    ) -> dict[str, object]:
        bounded_limit = min(max(int(limit), 1), 20)
        actions = CLIENT_VISIBLE_ACTIONS | (
            STAFF_ONLY_ACTIONS if audience == "staff" else frozenset()
        )
        statement = (
            select(AuditLog)
            .where(AuditLog.entity_type == "case")
            .where(AuditLog.entity_id == int(case_id))
            .where(AuditLog.action.in_(tuple(actions)))
            .order_by(AuditLog.id.desc())
        )
        if before_id is not None:
            statement = statement.where(AuditLog.id < int(before_id))
        rows = list(
            (
                await self.db.execute(statement.limit(bounded_limit + 1))
            ).scalars().all()
        )
        has_more = len(rows) > bounded_limit
        visible_rows = rows[:bounded_limit]
        items = [
            item
            for row in visible_rows
            if (item := present_case_activity(row, audience=audience)) is not None
        ]
        next_before_id = (
            int(visible_rows[-1].id)
            if has_more and visible_rows
            else None
        )
        return {
            "case_id": int(case_id),
            "audience": audience,
            "count": len(items),
            "has_more": has_more,
            "next_before_id": next_before_id,
            "items": [item.as_dict() for item in items],
        }
