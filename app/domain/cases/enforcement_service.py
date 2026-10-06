from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus


_MONEY_QUANT = Decimal("0.01")
_MAX_MONEY = Decimal("999999999999.99")


class EnforcementError(ValueError):
    pass


def normalize_received_amount(value: object) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise EnforcementError("Укажите корректную сумму поступления") from error
    if not amount.is_finite() or amount <= 0:
        raise EnforcementError("Сумма поступления должна быть больше нуля")
    if amount > _MAX_MONEY:
        raise EnforcementError("Сумма поступления превышает допустимый лимит")
    return amount


class EnforcementService:
    """Authoritative M1 execution facts and money-receipt workflow."""

    def __init__(self, db):
        self.db = db
        self.cases = CaseService(db)

    @staticmethod
    def _require_enforcement(case) -> None:
        if str(case.status) != CaseStatus.M1_ENFORCEMENT.value:
            raise EnforcementError(
                "Данные исполнения можно менять только на этапе исполнительного производства"
            )

    async def update_execution(
        self,
        *,
        case,
        enforcement_number: object | None,
        enforcement_status: object | None,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
    ):
        self._require_enforcement(case)
        number = str(enforcement_number or "").strip()
        status = str(enforcement_status or "").strip()
        if not number and not status:
            raise EnforcementError("Укажите номер ИП или статус исполнения")
        if len(number) > 255:
            raise EnforcementError("Номер исполнительного производства слишком длинный")
        if len(status) > 100:
            raise EnforcementError("Статус исполнительного производства слишком длинный")

        old = {
            "enforcement_number": case.enforcement_number,
            "enforcement_status": case.enforcement_status,
        }
        if number:
            case.enforcement_number = number
        if status:
            case.enforcement_status = status
        if case.enforcement_started_at is None:
            case.enforcement_started_at = datetime.now(timezone.utc)

        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="M1_ENFORCEMENT_UPDATED",
            old_value=old,
            new_value={
                "enforcement_number": case.enforcement_number,
                "enforcement_status": case.enforcement_status,
                "enforcement_started_at": case.enforcement_started_at.isoformat(),
            },
            comment=comment,
        )
        await self.db.flush()
        return case

    async def record_receipt(
        self,
        *,
        case,
        amount: object,
        final: bool,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
    ):
        self._require_enforcement(case)
        received = normalize_received_amount(amount)
        current = Decimal(str(case.received_amount or "0")).quantize(_MONEY_QUANT)
        total = current + received
        if total > _MAX_MONEY:
            raise EnforcementError("Суммарное поступление превышает допустимый лимит")

        case.received_amount = total
        now = datetime.now(timezone.utc)
        action = "M1_PARTIAL_RECEIPT_RECORDED"
        if final:
            case.received_at = now
            case.enforcement_status = "MONEY_RECEIVED"
            action = "M1_MONEY_RECEIVED_RECORDED"
        elif not case.enforcement_status or case.enforcement_status == "STARTED":
            case.enforcement_status = "PARTIAL_PAYMENT"

        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action=action,
            old_value={"received_amount": str(current)},
            new_value={
                "receipt_amount": str(received),
                "received_amount": str(total),
                "received_at": case.received_at.isoformat() if case.received_at else None,
                "final": bool(final),
            },
            comment=comment,
        )

        if final:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M1_MONEY_RECEIVED,
                actor_type=actor_type,
                actor_id=actor_id,
                comment=comment or "Фактическое поступление денег клиенту подтверждено",
            )

        await self.db.flush()
        return case
