from __future__ import annotations

import inspect

from app.bot import bot as bot_module
from app.bot.screens import fallback, payment_archive_guard
from app.domain.statuses.case_statuses import CaseStatus


def _callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def _urls(markup):
    return [
        button.url
        for row in markup.inline_keyboard
        for button in row
        if button.url
    ]


def test_fallback_unpacks_completed_case_state_and_restores_archive_menu():
    stale_source = inspect.getsource(fallback.stale_callback)
    unknown_source = inspect.getsource(fallback.unknown_message)

    expected_unpack = "text, case_exists, completed_case, primary_action = await _home_text"
    assert expected_unpack in stale_source
    assert expected_unpack in unknown_source
    assert "completed_case=completed_case" in stale_source
    assert "completed_case=completed_case" in unknown_source


def test_archived_payment_guard_recognizes_all_client_completed_statuses():
    class CaseStub:
        def __init__(self, status):
            self.status = status

    assert payment_archive_guard._case_is_completed(CaseStub(CaseStatus.M1_CLOSED))
    assert payment_archive_guard._case_is_completed(CaseStub(CaseStatus.M2_CLOSED))
    assert payment_archive_guard._case_is_completed(CaseStub(CaseStatus.ARCHIVED))
    assert not payment_archive_guard._case_is_completed(
        CaseStub(CaseStatus.M2_CONSULTATION_BOOKED)
    )


def test_archived_payment_keyboard_has_navigation_but_no_payment_mutation():
    markup = payment_archive_guard._archive_keyboard()
    callbacks = _callbacks(markup)

    assert callbacks == [
        "payments_open",
        "case_history_open",
        "my_case_open",
        "nav_home",
    ]
    assert not _urls(markup)
    assert not any(value.startswith("pay_fake_success:") for value in callbacks)
    assert not any(value.startswith("pay_start") for value in callbacks)


def test_archived_payment_guard_is_registered_before_regular_payments_router():
    source = inspect.getsource(bot_module.build_dispatcher)
    assert source.index("payment_archive_guard.router") < source.index("payments.router")
