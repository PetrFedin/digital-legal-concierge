from __future__ import annotations

from collections.abc import Iterable

from app.domain.statuses.case_statuses import CaseStatus


class CaseTransitionError(ValueError):
    """Raised when a case status transition violates the workflow contract."""


TERMINAL_STATUSES = frozenset(
    {
        CaseStatus.M1_CLOSED,
        CaseStatus.M2_CLOSED,
        CaseStatus.ARCHIVED,
    }
)

# Every normal transition is explicit. Administrative corrections remain possible
# through CaseService(force=True), where the actor and explanation are audited.
_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.NEW: frozenset(
        {
            CaseStatus.CALCULATOR_STARTED,
            CaseStatus.CALCULATED,
            CaseStatus.CLIENT_DECISION,
            CaseStatus.M1_DOCUMENTS_PENDING,
            CaseStatus.M2_DESCRIPTION_PENDING,
        }
    ),
    CaseStatus.CALCULATOR_STARTED: frozenset(
        {
            CaseStatus.CALCULATED,
            CaseStatus.M2_DESCRIPTION_PENDING,
        }
    ),
    CaseStatus.CALCULATED: frozenset(
        {
            CaseStatus.CLIENT_DECISION,
            CaseStatus.M1_DOCUMENTS_PENDING,
            CaseStatus.M2_DESCRIPTION_PENDING,
        }
    ),
    CaseStatus.CLIENT_DECISION: frozenset(
        {
            CaseStatus.CALCULATED,
            CaseStatus.M1_DOCUMENTS_PENDING,
            CaseStatus.M2_DESCRIPTION_PENDING,
        }
    ),
    CaseStatus.M1_DOCUMENTS_PENDING: frozenset(
        {
            CaseStatus.CALCULATED,
            CaseStatus.M1_DOCUMENTS_RECEIVED,
            CaseStatus.M1_LAWYER_REVIEW,
            CaseStatus.M2_DESCRIPTION_PENDING,
        }
    ),
    CaseStatus.M1_DOCUMENTS_RECEIVED: frozenset(
        {
            CaseStatus.M1_LAWYER_REVIEW,
            CaseStatus.M1_DOCS_REQUESTED,
            CaseStatus.M2_DESCRIPTION_PENDING,
        }
    ),
    CaseStatus.M1_LAWYER_REVIEW: frozenset(
        {
            CaseStatus.M1_DOCS_REQUESTED,
            CaseStatus.M1_ACCEPTED,
            CaseStatus.M1_REJECTED,
            CaseStatus.M2_DESCRIPTION_PENDING,
        }
    ),
    CaseStatus.M1_DOCS_REQUESTED: frozenset(
        {
            CaseStatus.M1_DOCUMENTS_RECEIVED,
            CaseStatus.M1_LAWYER_REVIEW,
            CaseStatus.M1_REJECTED,
            CaseStatus.M2_DESCRIPTION_PENDING,
        }
    ),
    CaseStatus.M1_ACCEPTED: frozenset({CaseStatus.M1_CONTRACT_READY}),
    CaseStatus.M1_REJECTED: frozenset(
        {CaseStatus.M1_CLOSED, CaseStatus.M2_DESCRIPTION_PENDING}
    ),
    CaseStatus.M1_CONTRACT_READY: frozenset(
        {CaseStatus.M1_WAITING_PAYMENT_30000}
    ),
    CaseStatus.M1_WAITING_PAYMENT_30000: frozenset(
        {CaseStatus.M1_PAYMENT_30000_RECEIVED}
    ),
    CaseStatus.M1_PAYMENT_30000_RECEIVED: frozenset(
        {CaseStatus.M1_POWER_OF_ATTORNEY}
    ),
    CaseStatus.M1_POWER_OF_ATTORNEY: frozenset({CaseStatus.M1_POA_RECEIVED}),
    CaseStatus.M1_POA_RECEIVED: frozenset(
        {CaseStatus.M1_CLAIM_PREPARATION, CaseStatus.M1_CLAIM_SENT}
    ),
    CaseStatus.M1_CLAIM_PREPARATION: frozenset({CaseStatus.M1_CLAIM_SENT}),
    CaseStatus.M1_CLAIM_SENT: frozenset({CaseStatus.M1_WAITING_30_DAYS}),
    CaseStatus.M1_WAITING_30_DAYS: frozenset(
        {CaseStatus.M1_COURT_STAGE, CaseStatus.M1_MONEY_RECEIVED}
    ),
    CaseStatus.M1_COURT_STAGE: frozenset(
        {
            CaseStatus.M1_WAITING_PAYMENT_70000,
        }
    ),
    CaseStatus.M1_WAITING_PAYMENT_70000: frozenset(
        {CaseStatus.M1_PAYMENT_70000_RECEIVED}
    ),
    CaseStatus.M1_PAYMENT_70000_RECEIVED: frozenset(
        {CaseStatus.M1_ENFORCEMENT}
    ),
    CaseStatus.M1_ENFORCEMENT: frozenset({CaseStatus.M1_MONEY_RECEIVED}),
    CaseStatus.M1_MONEY_RECEIVED: frozenset(
        {CaseStatus.M1_WAITING_SUCCESS_FEE}
    ),
    CaseStatus.M1_WAITING_SUCCESS_FEE: frozenset(
        {CaseStatus.M1_SUCCESS_FEE_RECEIVED}
    ),
    CaseStatus.M1_SUCCESS_FEE_RECEIVED: frozenset({CaseStatus.M1_CLOSED}),
    CaseStatus.M1_CLOSED: frozenset({CaseStatus.ARCHIVED}),
    CaseStatus.M2_CONSULTATION_ROUTE: frozenset(
        {CaseStatus.M2_DESCRIPTION_PENDING}
    ),
    CaseStatus.M2_DESCRIPTION_PENDING: frozenset(
        {CaseStatus.M2_DOCUMENTS_OPTIONAL, CaseStatus.M2_SLOT_PENDING}
    ),
    CaseStatus.M2_DOCUMENTS_OPTIONAL: frozenset({CaseStatus.M2_SLOT_PENDING}),
    CaseStatus.M2_SLOT_PENDING: frozenset(
        {CaseStatus.M2_PAYMENT_PENDING, CaseStatus.M2_CONSULTATION_BOOKED}
    ),
    CaseStatus.M2_PAYMENT_PENDING: frozenset(
        {CaseStatus.M2_SLOT_PENDING, CaseStatus.M2_CONSULTATION_BOOKED}
    ),
    CaseStatus.M2_CONSULTATION_BOOKED: frozenset(
        {
            CaseStatus.M2_SLOT_PENDING,
            CaseStatus.M2_CONSULTATION_DONE,
            CaseStatus.M2_CLOSED,
            CaseStatus.M1_DOCUMENTS_PENDING,
        }
    ),
    CaseStatus.M2_CONSULTATION_DONE: frozenset(
        {
            CaseStatus.M2_SLOT_PENDING,
            CaseStatus.M2_CONSULTATION_BOOKED,
            CaseStatus.M2_TO_M1,
            CaseStatus.M1_DOCUMENTS_PENDING,
            CaseStatus.M2_CLOSED,
        }
    ),
    CaseStatus.M2_TO_M1: frozenset({CaseStatus.M1_DOCUMENTS_PENDING}),
    CaseStatus.M2_CLOSED: frozenset({CaseStatus.ARCHIVED}),
    CaseStatus.ERROR: frozenset(),
    CaseStatus.ARCHIVED: frozenset(),
}

# A system failure may move an active case into the dedicated ERROR state, but
# terminal records stay immutable unless an administrator uses a forced change.
for _source in CaseStatus:
    if _source not in TERMINAL_STATUSES and _source != CaseStatus.ERROR:
        _TRANSITIONS[_source] = frozenset(
            {*_TRANSITIONS.get(_source, frozenset()), CaseStatus.ERROR}
        )


def normalize_status(value: str | CaseStatus) -> CaseStatus:
    try:
        return value if isinstance(value, CaseStatus) else CaseStatus(str(value))
    except ValueError as error:
        raise CaseTransitionError(f"Неизвестный статус дела: {value}") from error


def allowed_next_statuses(value: str | CaseStatus) -> frozenset[CaseStatus]:
    return _TRANSITIONS.get(normalize_status(value), frozenset())


def transition_allowed(
    current: str | CaseStatus,
    target: str | CaseStatus,
) -> bool:
    source = normalize_status(current)
    destination = normalize_status(target)
    return source == destination or destination in allowed_next_statuses(source)


def validate_transition(
    current: str | CaseStatus,
    target: str | CaseStatus,
    *,
    force: bool,
    actor_type: str,
    comment: str | None,
) -> tuple[CaseStatus, CaseStatus]:
    source = normalize_status(current)
    destination = normalize_status(target)
    if source == destination:
        return source, destination
    if force:
        if actor_type not in {"admin", "system"}:
            raise CaseTransitionError(
                "Принудительное изменение статуса доступно только администратору "
                "или системному процессу"
            )
        if len(str(comment or "").strip()) < 5:
            raise CaseTransitionError(
                "Для принудительного изменения статуса нужен содержательный комментарий"
            )
        return source, destination
    if destination not in allowed_next_statuses(source):
        allowed = ", ".join(
            item.value
            for item in sorted(
                allowed_next_statuses(source), key=lambda item: item.value
            )
        ) or "нет"
        raise CaseTransitionError(
            f"Недопустимый переход {source.value} -> {destination.value}. "
            f"Разрешено: {allowed}"
        )
    return source, destination


def assert_policy_complete(statuses: Iterable[CaseStatus] = CaseStatus) -> None:
    missing = [status.value for status in statuses if status not in _TRANSITIONS]
    if missing:
        raise AssertionError(
            "Для статусов отсутствует политика переходов: " + ", ".join(missing)
        )
