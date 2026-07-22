from __future__ import annotations

from dataclasses import dataclass

from app.domain.cases.case_service import CaseService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode


class M1PaymentLifecycleError(RuntimeError):
    """A successful M1 payment cannot be safely applied to the case."""


@dataclass(frozen=True)
class M1PaymentPlan:
    entry_statuses: frozenset[str]
    transitions: tuple[str, ...]


M1_STATUS_ORDER = (
    CaseStatus.M1_DOCUMENTS_PENDING.value,
    CaseStatus.M1_DOCUMENTS_RECEIVED.value,
    CaseStatus.M1_LAWYER_REVIEW.value,
    CaseStatus.M1_DOCS_REQUESTED.value,
    CaseStatus.M1_ACCEPTED.value,
    CaseStatus.M1_CONTRACT_READY.value,
    CaseStatus.M1_WAITING_PAYMENT_30000.value,
    CaseStatus.M1_PAYMENT_30000_RECEIVED.value,
    CaseStatus.M1_POWER_OF_ATTORNEY.value,
    CaseStatus.M1_POA_RECEIVED.value,
    CaseStatus.M1_CLAIM_PREPARATION.value,
    CaseStatus.M1_CLAIM_SENT.value,
    CaseStatus.M1_WAITING_30_DAYS.value,
    CaseStatus.M1_COURT_STAGE.value,
    CaseStatus.M1_WAITING_PAYMENT_70000.value,
    CaseStatus.M1_PAYMENT_70000_RECEIVED.value,
    CaseStatus.M1_ENFORCEMENT.value,
    CaseStatus.M1_MONEY_RECEIVED.value,
    CaseStatus.M1_WAITING_SUCCESS_FEE.value,
    CaseStatus.M1_SUCCESS_FEE_RECEIVED.value,
    CaseStatus.M1_CLOSED.value,
)

M1_STATUS_INDEX = {status: index for index, status in enumerate(M1_STATUS_ORDER)}

M1_PAYMENT_PLANS = {
    PaymentCode.M1_INITIAL_PAYMENT: M1PaymentPlan(
        entry_statuses=frozenset(
            {
                CaseStatus.M1_CONTRACT_READY.value,
                CaseStatus.M1_WAITING_PAYMENT_30000.value,
                CaseStatus.M1_PAYMENT_30000_RECEIVED.value,
            }
        ),
        transitions=(
            CaseStatus.M1_PAYMENT_30000_RECEIVED.value,
            CaseStatus.M1_POWER_OF_ATTORNEY.value,
        ),
    ),
    PaymentCode.M1_COURT_PAYMENT: M1PaymentPlan(
        entry_statuses=frozenset(
            {
                CaseStatus.M1_COURT_STAGE.value,
                CaseStatus.M1_WAITING_PAYMENT_70000.value,
                CaseStatus.M1_PAYMENT_70000_RECEIVED.value,
            }
        ),
        transitions=(
            CaseStatus.M1_PAYMENT_70000_RECEIVED.value,
            CaseStatus.M1_ENFORCEMENT.value,
        ),
    ),
    PaymentCode.M1_SUCCESS_FEE: M1PaymentPlan(
        entry_statuses=frozenset(
            {
                CaseStatus.M1_ENFORCEMENT.value,
                CaseStatus.M1_MONEY_RECEIVED.value,
                CaseStatus.M1_WAITING_SUCCESS_FEE.value,
                CaseStatus.M1_SUCCESS_FEE_RECEIVED.value,
            }
        ),
        transitions=(
            CaseStatus.M1_SUCCESS_FEE_RECEIVED.value,
            CaseStatus.M1_CLOSED.value,
        ),
    ),
}


class M1PaymentLifecycleService:
    """Apply M1 payment transitions without skipping unrelated case stages."""

    def __init__(self, db):
        self.db = db
        self.cases = CaseService(db)

    @staticmethod
    def _validate_route(case) -> None:
        if case is None:
            raise M1PaymentLifecycleError("Дело для обработки платежа не найдено.")
        if case.route not in {RouteCode.M1, RouteCode.M1.value}:
            raise M1PaymentLifecycleError(
                "Платёж маршрута М1 не относится к текущему маршруту дела."
            )

    @staticmethod
    def _plan(payment_code: str) -> M1PaymentPlan:
        plan = M1_PAYMENT_PLANS.get(payment_code)
        if plan is None:
            raise M1PaymentLifecycleError(
                "Для платежа не настроен сценарий маршрута М1."
            )
        return plan

    @staticmethod
    def _status_index(status: str) -> int:
        try:
            return M1_STATUS_INDEX[status]
        except KeyError as exc:
            raise M1PaymentLifecycleError(
                "Текущий статус дела не поддерживает автоматическую обработку платежа."
            ) from exc

    async def apply_successful_payment(
        self,
        *,
        case,
        payment_code: str,
        actor_type: str,
        actor_id: int | None,
        source: str,
    ) -> list[str]:
        self._validate_route(case)
        plan = self._plan(payment_code)
        current_index = self._status_index(case.status)
        final_index = self._status_index(plan.transitions[-1])

        if current_index > final_index:
            return []

        earliest_entry_index = min(
            self._status_index(status) for status in plan.entry_statuses
        )
        if current_index < earliest_entry_index:
            raise M1PaymentLifecycleError(
                "Платёж получен раньше допустимого этапа дела. Требуется проверка юристом."
            )

        if case.status not in plan.entry_statuses and current_index < final_index:
            raise M1PaymentLifecycleError(
                "Платёж не соответствует текущему этапу дела. Требуется ручная проверка."
            )

        applied: list[str] = []
        for next_status in plan.transitions:
            next_index = self._status_index(next_status)
            if current_index >= next_index:
                continue
            await self.cases.change_status(
                case=case,
                next_status=next_status,
                actor_type=actor_type,
                actor_id=actor_id,
                force=True,
                comment=f"Автопереход после оплаты {payment_code}; источник: {source}",
            )
            applied.append(next_status)
            current_index = next_index

        await self.db.flush()
        return applied
