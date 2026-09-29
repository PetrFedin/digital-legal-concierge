from __future__ import annotations

from app.domain.cases.case_transition_policy import normalize_status
from app.domain.statuses.case_statuses import CaseStatus

# Every product-stage status is owned by a concrete client/system/lawyer/payment
# or consultation action. A generic admin selector is not a second state machine.
# Keep the groups explicit so tests can verify that new enum values are either
# intentionally owned or fail the completeness contract below.
INTAKE_DOMAIN_MANAGED_STATUSES = frozenset(
    {
        CaseStatus.NEW,
        CaseStatus.CALCULATOR_STARTED,
        CaseStatus.CALCULATED,
        CaseStatus.CLIENT_DECISION,
    }
)

M1_DOMAIN_MANAGED_STATUSES = frozenset(
    {
        CaseStatus.M1_DOCUMENTS_PENDING,
        CaseStatus.M1_DOCUMENTS_RECEIVED,
        CaseStatus.M1_LAWYER_REVIEW,
        CaseStatus.M1_DOCS_REQUESTED,
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

SELF_FILING_DOMAIN_MANAGED_STATUSES = frozenset(
    {
        CaseStatus.M1_SELF_FILING_PROFILE_PENDING,
        CaseStatus.M1_SELF_FILING_DOCUMENTS_PENDING,
        CaseStatus.M1_SELF_FILING_DOCUMENTS_RECEIVED,
        CaseStatus.M1_SELF_FILING_LAWYER_REVIEW,
        CaseStatus.M1_SELF_FILING_DOCS_REQUESTED,
        CaseStatus.M1_SELF_FILING_PAYMENT_PENDING,
        CaseStatus.M1_SELF_FILING_PREPARATION,
        CaseStatus.M1_SELF_FILING_READY,
        CaseStatus.M1_SELF_FILING_DELIVERED,
        CaseStatus.M1_SELF_FILING_CLOSED,
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

LIFECYCLE_DOMAIN_MANAGED_STATUSES = frozenset(
    {
        CaseStatus.ERROR,
        CaseStatus.ARCHIVED,
    }
)

DOMAIN_MANAGED_STATUSES = (
    INTAKE_DOMAIN_MANAGED_STATUSES
    | M1_DOMAIN_MANAGED_STATUSES
    | SELF_FILING_DOMAIN_MANAGED_STATUSES
    | M2_DOMAIN_MANAGED_STATUSES
    | LIFECYCLE_DOMAIN_MANAGED_STATUSES
)

# Fail safe when the state machine grows: a newly introduced status must not
# silently become editable through the generic admin selector merely because the
# ownership policy forgot to list it.
if DOMAIN_MANAGED_STATUSES != frozenset(CaseStatus):
    missing = sorted(status.value for status in frozenset(CaseStatus) - DOMAIN_MANAGED_STATUSES)
    extra = sorted(status.value for status in DOMAIN_MANAGED_STATUSES - frozenset(CaseStatus))
    raise RuntimeError(
        "Admin manual-status ownership policy is incomplete: "
        f"missing={missing}, extra={extra}"
    )

# Compatibility name retained for older imports/tests. The final financial
# subset is protected as part of the broader managed workflow set.
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

    # Rejected M1 cases may be closed by an administrator as the one explicitly
    # retained compatibility action. It is a normal rejection close, not a
    # success-fee or legal-outcome shortcut.
    if source == CaseStatus.M1_REJECTED and destination == CaseStatus.M1_CLOSED:
        return True

    # All other states are owned by their domain actions. ERROR recovery and
    # archive/retention also have dedicated lifecycle services; generic status
    # editing must not create or escape those states.
    return False


def assert_manual_status_change_allowed(
    current: str | CaseStatus,
    target: str | CaseStatus,
) -> None:
    if manual_status_change_allowed(current, target):
        return
    raise ValueError(
        "Статус дела управляется конкретным действием клиента, системы, юриста, "
        "платёжного или консультационного контура и не может быть создан generic-сменой. "
        "Используйте соответствующий рабочий сценарий; ERROR и архив восстанавливаются "
        "только через отдельные lifecycle-механизмы."
    )
