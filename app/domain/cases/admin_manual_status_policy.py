from __future__ import annotations

from app.domain.cases.case_transition_policy import normalize_status
from app.domain.statuses.case_statuses import CaseStatus

# These statuses are not administrative labels. They prove that a domain action
# happened: the lawyer recorded actual recovery, the success-fee payment was
# opened, the provider confirmed it, and the case was closed by that payment.
M1_FINANCIAL_MANAGED_STATUSES = frozenset(
    {
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
        CaseStatus.M1_SUCCESS_FEE_RECEIVED,
        CaseStatus.M1_CLOSED,
    }
)


def manual_status_change_allowed(
    current: str | CaseStatus,
    target: str | CaseStatus,
) -> bool:
    source = normalize_status(current)
    destination = normalize_status(target)

    # Rejected M1 cases may be closed without a recovery/payment flow. This is
    # an explicit normal transition and is not a success-fee closure.
    if source == CaseStatus.M1_REJECTED and destination == CaseStatus.M1_CLOSED:
        return True

    if source in M1_FINANCIAL_MANAGED_STATUSES:
        return False
    if destination in M1_FINANCIAL_MANAGED_STATUSES:
        return False
    return True


def assert_manual_status_change_allowed(
    current: str | CaseStatus,
    target: str | CaseStatus,
) -> None:
    if manual_status_change_allowed(current, target):
        return
    raise ValueError(
        "Финальный финансовый контур M1 нельзя менять вручную. "
        "Фактическое взыскание фиксирует назначенный юрист, success fee создаётся "
        "из этой суммы, а подтверждение оплаты и закрытие выполняет платёжный webhook."
    )
