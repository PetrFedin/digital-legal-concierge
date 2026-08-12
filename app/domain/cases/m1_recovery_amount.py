from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

MONEY_RECEIVED_AUDIT_ACTION = "M1_MONEY_RECEIVED_RECORDED"
RUB_CENT = Decimal("0.01")


def normalize_recovered_amount(value: object) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(RUB_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Укажите корректную фактически взысканную сумму") from error
    if amount <= 0:
        raise ValueError("Фактически взысканная сумма должна быть больше нуля")
    return amount


async def load_recovered_amount(
    db: AsyncSession,
    *,
    case_id: int,
) -> Decimal | None:
    """Load the latest immutable lawyer-recorded recovered amount for M1."""

    event = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.entity_type == "case")
            .where(AuditLog.entity_id == int(case_id))
            .where(AuditLog.action == MONEY_RECEIVED_AUDIT_ACTION)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if event is None:
        return None
    payload = event.new_value or {}
    raw_amount = payload.get("amount")
    if raw_amount is None:
        raise ValueError("В истории дела не указана фактически взысканная сумма")
    return normalize_recovered_amount(raw_amount)
