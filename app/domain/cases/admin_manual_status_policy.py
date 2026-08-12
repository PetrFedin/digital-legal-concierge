from __future__ import annotations

from app.domain.cases.case_transition_policy import normalize_status
from app.domain.statuses.case_statuses import CaseStatus

# These stages represent real legal, consultation or payment facts. A generic
# admin status switch must never manufacture them: each has a dedicated domain
# action (lawyer decision, document/POA receipt, court evidence, consultation
# booking/outcome, payment webhook/offline confirmation, recovery, etc.).
M1_DOMAIN_MANAGED_STATUSES = frozenset(
    {
        CaseStatus.M1_ACCEPTED,
        CaseStatus.M1_REJECTED,
        CaseStatus.M1_CONTRACT_READY,
        CaseStatus.M1_WAITING_PAYMENT_30000,
        CaseStatus.M1_PAYMENT_30000_RECEIVED,
        CaseStatus.M1_POWER_OF_ATTORNEY,
        CaseStatus.M1_POA_RECEIVED,
        CaseStatus.M1_CLAIM_PREPARATION,
        CaseStatus.M1_CLAIM_SENT,
        CaseStatus.M1_WAITING_30_DAYS,
        CaseStatus.M1_COURT_STAGE,
        CaseStatus.M1_WAITING_PAYMENT_70000,
        CaseStatus.M1_PAYMENT_70000_RECEIVED,
        CaseStatus.M1_ENFORCEMENT,
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
        CaseStatus.M1_SUCCESS_FEE_RECEIVED,
        CaseStatus.M1_CLOSED,
    }
)

M2_DOMAIN_MANAGED_STATUSES = frozenset(
    {
        CaseStatus.M2_CONSULTATION_ROUTE,
        CaseStatus.M2_DESCRIPTION_PENDING,
        CaseStatus.M2_DOCUMENTS_OPTIONAL,
        CaseStatus.M2_SLOT_PENDING,
        CaseStatus.M2_PAYMENT_PENDING,
        CaseStatus.M2_CONSULTATION_BOOKED,
        CaseStatus.M2_CONSULTATION_DONE,
        CaseStatus.M2_TO_M1,
        CaseStatus.M2_CLOSED,
    }
)

DOMAIN_MANAGED_STATUSES = M1_DOMAIN_MANAGED_STATUSES | M2_DOMAIN_MANAGED_STATUSES
# Compatibility name retained for older imports/tests. The final financial
# subset is still protected, now as part of the broader managed workflow set.
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

    # ERROR is not a business stage and must not become a generic status switch.
    # Recovery is derived from the audited transition that entered ERROR and is
    # exposed by CaseErrorRecoveryService only for a narrow set of case-owned
    # stages. Payment and consultation truth remains in their domain ledgers.
    if source == CaseStatus.ERROR:
        return False

    # Rejected M1 cases may be closed without a recovery/payment flow. This is
    # an explicit normal transition and is not a success-fee closure.
    if source == CaseStatus.M1_REJECTED and destination == CaseStatus.M1_CLOSED:
        return True

    if source in DOMAIN_MANAGED_STATUSES:
        return False
    if destination in DOMAIN_MANAGED_STATUSES:
        return False
    return True


def assert_manual_status_change_allowed(
    current: str | CaseStatus,
    target: str | CaseStatus,
) -> None:
    source = normalize_status(current)
    if source == CaseStatus.ERROR:
        raise ValueError(
            "Технический статус ERROR нельзя менять вручную. Используйте безопасное "
            "восстановление по последнему подтверждённому этапу в технической карточке дела."
        )
    if manual_status_change_allowed(current, target):
        return
    raise ValueError(
        "Этот этап M1/M2 подтверждает реальное юридическое, консультационное или "
        "финансовое событие и не может быть создан generic-сменой статуса. "
        "Используйте соответствующее действие в рабочем кабинете: решение юриста, "
        "подтверждение документа/доверенности, судебный акт, консультацию или платёжный контур."
    )
