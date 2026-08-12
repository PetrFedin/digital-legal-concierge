from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.models.case import Case

COURT_DECISION_RECORDED_ACTION = "M1_COURT_DECISION_RECORDED"


def normalize_court_decision_reference(value: object) -> str:
    reference = " ".join(str(value or "").split())
    if len(reference) < 5:
        raise ValueError(
            "Укажите номер дела, решения или иной идентификатор судебного акта — минимум 5 символов"
        )
    if len(reference) > 240:
        raise ValueError("Идентификатор судебного акта слишком длинный")
    return reference


def normalize_court_decision_date(value: object) -> date:
    raw = str(value or "").strip()
    try:
        decision_date = date.fromisoformat(raw)
    except ValueError as error:
        raise ValueError("Укажите дату судебного акта в формате ГГГГ-ММ-ДД") from error
    today = datetime.now(timezone.utc).date()
    if decision_date > today:
        raise ValueError("Дата судебного акта не может быть в будущем")
    return decision_date


async def record_court_decision_evidence(
    db: AsyncSession,
    *,
    case: Case,
    lawyer_id: int,
    decision_reference: object,
    decision_date: object,
    comment: str,
) -> tuple[str, date]:
    reference = normalize_court_decision_reference(decision_reference)
    occurred_on = normalize_court_decision_date(decision_date)
    await add_case_history_event(
        db,
        actor_type="lawyer",
        actor_id=lawyer_id,
        case_id=case.id,
        action=COURT_DECISION_RECORDED_ACTION,
        new_value={
            "decision_reference": reference,
            "decision_date": occurred_on.isoformat(),
            "case_status": str(case.status),
        },
        comment=comment,
    )
    return reference, occurred_on


__all__ = [
    "COURT_DECISION_RECORDED_ACTION",
    "normalize_court_decision_date",
    "normalize_court_decision_reference",
    "record_court_decision_evidence",
]
