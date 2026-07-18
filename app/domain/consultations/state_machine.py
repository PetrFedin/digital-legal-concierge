from __future__ import annotations

from app.domain.statuses.consultation_statuses import ConsultationStatus


class ConsultationStateMachineError(ValueError):
    """Base error for consultation state machine failures."""


class InvalidConsultationStatus(ConsultationStateMachineError):
    """Raised when a value is not a known consultation status."""


class InvalidConsultationTransition(ConsultationStateMachineError):
    """Raised when a consultation status transition is not allowed."""


class TerminalConsultationTransition(InvalidConsultationTransition):
    """Raised when attempting to leave a terminal consultation status."""


TERMINAL_STATES: frozenset[ConsultationStatus] = frozenset(
    {
        ConsultationStatus.DECLINED,
        ConsultationStatus.CANCELLED,
        ConsultationStatus.CLOSED,
    }
)


_ALLOWED_TRANSITIONS: dict[ConsultationStatus, frozenset[ConsultationStatus]] = {
    ConsultationStatus.DESCRIPTION_PENDING: frozenset(
        {
            ConsultationStatus.DOCUMENTS_OPTIONAL,
            ConsultationStatus.CANCELLED,
        }
    ),
    ConsultationStatus.DOCUMENTS_OPTIONAL: frozenset(
        {
            ConsultationStatus.SLOT_PENDING,
            ConsultationStatus.CANCELLED,
        }
    ),
    ConsultationStatus.SLOT_PENDING: frozenset(
        {
            ConsultationStatus.SLOT_RESERVED,
            ConsultationStatus.CANCELLED,
        }
    ),
    ConsultationStatus.SLOT_RESERVED: frozenset(
        {
            ConsultationStatus.PAYMENT_PENDING,
            ConsultationStatus.SLOT_PENDING,
            ConsultationStatus.CANCELLED,
        }
    ),
    ConsultationStatus.PAYMENT_PENDING: frozenset(
        {
            ConsultationStatus.PAID_PENDING_CONFIRMATION,
            ConsultationStatus.SLOT_PENDING,
            ConsultationStatus.CANCELLED,
        }
    ),
    ConsultationStatus.PAID_PENDING_CONFIRMATION: frozenset(
        {
            ConsultationStatus.CONFIRMED,
            ConsultationStatus.DECLINED,
        }
    ),
    ConsultationStatus.CONFIRMED: frozenset({ConsultationStatus.BOOKED}),
    ConsultationStatus.DECLINED: frozenset(),
    ConsultationStatus.BOOKED: frozenset({ConsultationStatus.DONE}),
    ConsultationStatus.DONE: frozenset({ConsultationStatus.CLOSED}),
    ConsultationStatus.RESCHEDULED: frozenset(),
    ConsultationStatus.CANCELLED: frozenset(),
    ConsultationStatus.CLOSED: frozenset(),
}


def _as_status(value: ConsultationStatus | str) -> ConsultationStatus:
    if isinstance(value, ConsultationStatus):
        return value
    try:
        return ConsultationStatus(value)
    except (TypeError, ValueError) as exc:
        raise InvalidConsultationStatus(
            f"Unknown consultation status: {value!r}"
        ) from exc


class ConsultationStateMachine:
    """Validate and apply consultation status transitions."""

    @staticmethod
    def is_terminal(status: ConsultationStatus | str) -> bool:
        return _as_status(status) in TERMINAL_STATES

    @staticmethod
    def can_transition(
        current_status: ConsultationStatus | str,
        next_status: ConsultationStatus | str,
    ) -> bool:
        current = _as_status(current_status)
        target = _as_status(next_status)
        return current == target or target in _ALLOWED_TRANSITIONS[current]

    @staticmethod
    def validate_transition(
        current_status: ConsultationStatus | str,
        next_status: ConsultationStatus | str,
    ) -> None:
        current = _as_status(current_status)
        target = _as_status(next_status)

        if current == target:
            return
        if current in TERMINAL_STATES:
            raise TerminalConsultationTransition(
                f"Cannot transition consultation from terminal status "
                f"{current.value} to {target.value}."
            )
        if target not in _ALLOWED_TRANSITIONS[current]:
            allowed = ", ".join(
                status.value for status in sorted(_ALLOWED_TRANSITIONS[current], key=str)
            )
            raise InvalidConsultationTransition(
                f"Cannot transition consultation from {current.value} to "
                f"{target.value}. Allowed transitions: {allowed or 'none'}."
            )

    @staticmethod
    def transition(
        current_status: ConsultationStatus | str,
        next_status: ConsultationStatus | str,
    ) -> ConsultationStatus:
        ConsultationStateMachine.validate_transition(current_status, next_status)
        return _as_status(next_status)


def is_terminal(status: ConsultationStatus | str) -> bool:
    return ConsultationStateMachine.is_terminal(status)


def can_transition(
    current_status: ConsultationStatus | str,
    next_status: ConsultationStatus | str,
) -> bool:
    return ConsultationStateMachine.can_transition(current_status, next_status)


def validate_transition(
    current_status: ConsultationStatus | str,
    next_status: ConsultationStatus | str,
) -> None:
    ConsultationStateMachine.validate_transition(current_status, next_status)


def transition(
    current_status: ConsultationStatus | str,
    next_status: ConsultationStatus | str,
) -> ConsultationStatus:
    return ConsultationStateMachine.transition(current_status, next_status)
