import pytest

from app.domain.consultations.state_machine import (
    _ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    ConsultationStateMachine,
    InvalidConsultationStatus,
    InvalidConsultationTransition,
    TerminalConsultationTransition,
    can_transition,
    is_terminal,
    transition,
    validate_transition,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus


EXPECTED_TRANSITIONS = {
    ConsultationStatus.DESCRIPTION_PENDING: {
        ConsultationStatus.DOCUMENTS_OPTIONAL,
        ConsultationStatus.CANCELLED,
    },
    ConsultationStatus.DOCUMENTS_OPTIONAL: {
        ConsultationStatus.SLOT_PENDING,
        ConsultationStatus.CANCELLED,
    },
    ConsultationStatus.SLOT_PENDING: {
        ConsultationStatus.SLOT_RESERVED,
        ConsultationStatus.CANCELLED,
    },
    ConsultationStatus.SLOT_RESERVED: {
        ConsultationStatus.PAYMENT_PENDING,
        ConsultationStatus.SLOT_PENDING,
        ConsultationStatus.CANCELLED,
    },
    ConsultationStatus.PAYMENT_PENDING: {
        ConsultationStatus.PAID_PENDING_CONFIRMATION,
        ConsultationStatus.SLOT_PENDING,
        ConsultationStatus.CANCELLED,
    },
    ConsultationStatus.PAID_PENDING_CONFIRMATION: {
        ConsultationStatus.CONFIRMED,
        ConsultationStatus.DECLINED,
    },
    ConsultationStatus.CONFIRMED: {ConsultationStatus.BOOKED},
    ConsultationStatus.DECLINED: set(),
    ConsultationStatus.BOOKED: {ConsultationStatus.DONE},
    ConsultationStatus.DONE: {ConsultationStatus.CLOSED},
    ConsultationStatus.CANCELLED: set(),
    ConsultationStatus.RESCHEDULED: set(),
    ConsultationStatus.CLOSED: set(),
}

CANCELLABLE_STATES = {
    ConsultationStatus.DESCRIPTION_PENDING,
    ConsultationStatus.DOCUMENTS_OPTIONAL,
    ConsultationStatus.SLOT_PENDING,
    ConsultationStatus.SLOT_RESERVED,
    ConsultationStatus.PAYMENT_PENDING,
}


def test_transition_table_covers_every_consultation_status_exactly_once():
    assert len(_ALLOWED_TRANSITIONS) == len(ConsultationStatus)
    assert set(_ALLOWED_TRANSITIONS) == set(ConsultationStatus)
    assert {
        status: set(next_statuses)
        for status, next_statuses in _ALLOWED_TRANSITIONS.items()
    } == EXPECTED_TRANSITIONS


@pytest.mark.parametrize(
    ("current_status", "next_status"),
    [
        (ConsultationStatus.DESCRIPTION_PENDING, ConsultationStatus.DOCUMENTS_OPTIONAL),
        (ConsultationStatus.DOCUMENTS_OPTIONAL, ConsultationStatus.SLOT_PENDING),
        (ConsultationStatus.SLOT_PENDING, ConsultationStatus.SLOT_RESERVED),
        (ConsultationStatus.SLOT_RESERVED, ConsultationStatus.PAYMENT_PENDING),
        (ConsultationStatus.SLOT_RESERVED, ConsultationStatus.SLOT_PENDING),
        (ConsultationStatus.PAYMENT_PENDING, ConsultationStatus.PAID_PENDING_CONFIRMATION),
        (ConsultationStatus.PAYMENT_PENDING, ConsultationStatus.SLOT_PENDING),
        (ConsultationStatus.PAID_PENDING_CONFIRMATION, ConsultationStatus.CONFIRMED),
        (ConsultationStatus.PAID_PENDING_CONFIRMATION, ConsultationStatus.DECLINED),
        (ConsultationStatus.CONFIRMED, ConsultationStatus.BOOKED),
        (ConsultationStatus.BOOKED, ConsultationStatus.DONE),
        (ConsultationStatus.DONE, ConsultationStatus.CLOSED),
    ],
)
def test_allows_expected_transitions(current_status, next_status):
    assert can_transition(current_status, next_status)
    assert transition(current_status, next_status) is next_status


@pytest.mark.parametrize(
    "current_status",
    list(ConsultationStatus),
)
def test_states_can_be_cancelled_only_before_confirmed_payment(current_status):
    if current_status in CANCELLABLE_STATES:
        assert can_transition(current_status, ConsultationStatus.CANCELLED) is True
        validate_transition(current_status, ConsultationStatus.CANCELLED)
        assert transition(current_status, ConsultationStatus.CANCELLED) is ConsultationStatus.CANCELLED
    elif current_status is ConsultationStatus.CANCELLED:
        assert can_transition(current_status, ConsultationStatus.CANCELLED) is True
        validate_transition(current_status, ConsultationStatus.CANCELLED)
        assert transition(current_status, ConsultationStatus.CANCELLED) is ConsultationStatus.CANCELLED
    else:
        assert can_transition(current_status, ConsultationStatus.CANCELLED) is False
        with pytest.raises(InvalidConsultationTransition):
            validate_transition(current_status, ConsultationStatus.CANCELLED)


@pytest.mark.parametrize("status", list(ConsultationStatus))
def test_same_status_transition_is_idempotent(status):
    assert can_transition(status, status)
    validate_transition(status, status)
    assert transition(status, status) is status


def test_accepts_persisted_string_statuses():
    assert ConsultationStateMachine.can_transition("PAYMENT_PENDING", "PAID_PENDING_CONFIRMATION")
    assert (
        ConsultationStateMachine.transition("PAYMENT_PENDING", "PAID_PENDING_CONFIRMATION")
        is ConsultationStatus.PAID_PENDING_CONFIRMATION
    )


def test_can_transition_returns_bool_for_every_valid_status_pair():
    for current_status in ConsultationStatus:
        for next_status in ConsultationStatus:
            result = can_transition(current_status, next_status)
            expected = (
                current_status is next_status
                or next_status in EXPECTED_TRANSITIONS[current_status]
            )
            assert type(result) is bool
            assert result is expected


@pytest.mark.parametrize(
    "status",
    [
        ConsultationStatus.DECLINED,
        ConsultationStatus.CANCELLED,
        ConsultationStatus.CLOSED,
    ],
)
def test_terminal_states_are_reported_and_cannot_be_left(status):
    assert is_terminal(status)
    with pytest.raises(TerminalConsultationTransition, match="terminal status"):
        transition(status, ConsultationStatus.DESCRIPTION_PENDING)


def test_non_terminal_state_is_not_reported_as_terminal():
    assert not is_terminal(ConsultationStatus.DONE)
    assert not is_terminal(ConsultationStatus.RESCHEDULED)


@pytest.mark.parametrize(
    ("current_status", "next_status"),
    [
        (ConsultationStatus.PAID_PENDING_CONFIRMATION, ConsultationStatus.CANCELLED),
        (ConsultationStatus.DECLINED, ConsultationStatus.SLOT_PENDING),
        (ConsultationStatus.DECLINED, ConsultationStatus.CANCELLED),
        (ConsultationStatus.DECLINED, ConsultationStatus.CLOSED),
        (ConsultationStatus.CONFIRMED, ConsultationStatus.RESCHEDULED),
        (ConsultationStatus.RESCHEDULED, ConsultationStatus.SLOT_PENDING),
        (ConsultationStatus.RESCHEDULED, ConsultationStatus.SLOT_RESERVED),
    ],
    ids=[
        "paid-pending-cannot-cancel",
        "declined-cannot-return-to-slot-selection",
        "declined-cannot-cancel",
        "declined-cannot-close",
        "confirmed-cannot-reschedule",
        "rescheduled-cannot-return-to-slot-selection",
        "rescheduled-cannot-reserve-slot",
    ],
)
def test_rejects_explicitly_forbidden_post_payment_transitions(
    current_status,
    next_status,
):
    assert can_transition(current_status, next_status) is False
    with pytest.raises(InvalidConsultationTransition):
        validate_transition(current_status, next_status)


@pytest.mark.parametrize(
    "current_status",
    [
        ConsultationStatus.PAID_PENDING_CONFIRMATION,
        ConsultationStatus.CONFIRMED,
        ConsultationStatus.DECLINED,
        ConsultationStatus.BOOKED,
        ConsultationStatus.DONE,
        ConsultationStatus.RESCHEDULED,
        ConsultationStatus.CLOSED,
    ],
)
def test_cancellation_is_forbidden_after_payment(current_status):
    assert can_transition(current_status, ConsultationStatus.CANCELLED) is False
    with pytest.raises(InvalidConsultationTransition):
        validate_transition(current_status, ConsultationStatus.CANCELLED)


def test_documents_cannot_skip_slot_selection_and_reservation():
    assert can_transition(
        ConsultationStatus.DOCUMENTS_OPTIONAL,
        ConsultationStatus.PAYMENT_PENDING,
    ) is False
    with pytest.raises(InvalidConsultationTransition):
        validate_transition(
            ConsultationStatus.DOCUMENTS_OPTIONAL,
            ConsultationStatus.PAYMENT_PENDING,
        )


def test_rejects_disallowed_transition_with_context():
    with pytest.raises(InvalidConsultationTransition) as exc_info:
        validate_transition(ConsultationStatus.DESCRIPTION_PENDING, ConsultationStatus.BOOKED)

    message = str(exc_info.value)
    assert "DESCRIPTION_PENDING" in message
    assert "BOOKED" in message
    assert "Allowed transitions" in message


@pytest.mark.parametrize("value", ["NOT_A_STATUS", "", None, 123])
@pytest.mark.parametrize("unknown_is_current", [True, False])
def test_rejects_unknown_statuses(value, unknown_is_current):
    current_status = value if unknown_is_current else ConsultationStatus.BOOKED
    next_status = ConsultationStatus.BOOKED if unknown_is_current else value

    with pytest.raises(InvalidConsultationStatus, match="Unknown consultation status"):
        can_transition(current_status, next_status)
