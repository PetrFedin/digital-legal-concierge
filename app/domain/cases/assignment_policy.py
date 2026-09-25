from __future__ import annotations

from app.domain.statuses.case_statuses import CaseStatus

# Automatic case assignment is an operational M1 concern only after the client
# has actually supplied documents. Earlier calculator/client-decision states are
# still owned by the client, while M2 responsibility is determined by the
# lawyer-specific consultation slot. Treating every active unassigned case as a
# queue item creates false work, starts SLA too early and can assign a different
# lawyer from the one booked for an M2 consultation.
AUTO_ASSIGNMENT_REQUIRED_STATUSES = frozenset(
    {
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
        CaseStatus.M1_SELF_FILING_DOCUMENTS_RECEIVED,
        CaseStatus.M1_SELF_FILING_LAWYER_REVIEW,
        CaseStatus.M1_SELF_FILING_DOCS_REQUESTED,
        CaseStatus.M1_SELF_FILING_PAYMENT_PENDING,
        CaseStatus.M1_SELF_FILING_PREPARATION,
        CaseStatus.M1_SELF_FILING_READY,
        CaseStatus.M1_SELF_FILING_DELIVERED,
    }
)
AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES = tuple(
    sorted(status.value for status in AUTO_ASSIGNMENT_REQUIRED_STATUSES)
)


def automatic_assignment_required(status: str | CaseStatus) -> bool:
    try:
        normalized = status if isinstance(status, CaseStatus) else CaseStatus(str(status))
    except ValueError:
        return False
    return normalized in AUTO_ASSIGNMENT_REQUIRED_STATUSES


__all__ = [
    "AUTO_ASSIGNMENT_REQUIRED_STATUSES",
    "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES",
    "automatic_assignment_required",
]
