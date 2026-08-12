from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.cases.m1_recovery_amount import (
    MONEY_RECEIVED_AUDIT_ACTION,
    load_recovered_amount,
    normalize_recovered_amount,
)
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case
from app.models.payment import Payment


@dataclass(frozen=True)
class MoneyReceivedResult:
    case: Case
    recovered_amount: Decimal
    success_fee_amount: Decimal
    payment: Payment


class M1EnforcementService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)
        self.payments = PaymentService(db)

    @staticmethod
    def _status(case: Case) -> CaseStatus:
        try:
            return CaseStatus(str(case.status))
        except ValueError as error:
            raise ValueError(f"Неизвестный статус дела: {case.status}") from error

    @staticmethod
    def _assert_assigned(case: Case, lawyer_id: int) -> None:
        if int(case.assigned_lawyer_id or 0) != int(lawyer_id):
            raise ValueError("Дело не назначено текущему юристу")

    async def record_money_received(
        self,
        *,
        case: Case,
        lawyer_id: int,
        amount: object,
        comment: str | None = None,
    ) -> MoneyReceivedResult:
        self._assert_assigned(case, lawyer_id)
        status = self._status(case)
        if status not in {CaseStatus.M1_ENFORCEMENT, CaseStatus.M1_MONEY_RECEIVED}:
            raise ValueError(
                "Поступление денег можно фиксировать только на исполнительном этапе"
            )
        existing = await load_recovered_amount(self.db, case_id=case.id)
        if existing is not None:
            raise ValueError(
                f"Фактически взысканная сумма уже зафиксирована: {existing} ₽"
            )
        recovered = normalize_recovered_amount(amount)
        clean_comment = str(comment or "").strip()

        if status == CaseStatus.M1_ENFORCEMENT:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M1_MONEY_RECEIVED,
                actor_type="lawyer",
                actor_id=lawyer_id,
                comment=clean_comment or "Юрист подтвердил фактическое поступление денег",
            )

        await add_case_history_event(
            self.db,
            actor_type="lawyer",
            actor_id=lawyer_id,
            case_id=case.id,
            action=MONEY_RECEIVED_AUDIT_ACTION,
            new_value={"amount": str(recovered), "currency": "RUB"},
            comment=clean_comment or "Зафиксирована фактически взысканная сумма",
        )

        success_fee = await self.payments.estimate_success_fee_for_case(case.id)
        payment = await self.payments.get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M1_SUCCESS_FEE,
            amount=success_fee,
        )
        if self._status(case) == CaseStatus.M1_MONEY_RECEIVED:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M1_WAITING_SUCCESS_FEE,
                actor_type="lawyer",
                actor_id=lawyer_id,
                comment=(
                    f"Фактически взыскано {recovered} ₽. "
                    f"Открыт success fee {success_fee} ₽."
                ),
            )
        return MoneyReceivedResult(
            case=case,
            recovered_amount=recovered,
            success_fee_amount=success_fee,
            payment=payment,
        )
