from __future__ import annotations

import inspect

from app.bot.client_case_view import CLIENT_ACTIONS
from app.bot.screens import contact_lawyer_scope_guard, m1_rejection_recovery


def test_rejected_m1_case_card_describes_the_actual_decision_center():
    action = CLIENT_ACTIONS["M1_REJECTED"]

    assert action.label == "Выбрать, что делать дальше"
    assert action.callback == "contact_lawyer"
    assert "перейти к консультации" in action.description
    assert "завершить обращение" in action.description
    assert "написать команде" in action.description


def test_rejected_m1_contact_callback_routes_to_rejection_decision_center():
    source = inspect.getsource(contact_lawyer_scope_guard.scoped_contact_lawyer)

    assert "_status(case) == CaseStatus.M1_REJECTED" in source
    assert "m1_rejection_recovery._show_options(callback, case)" in source


def test_rejected_m1_decision_center_keeps_all_safe_next_steps():
    source = inspect.getsource(m1_rejection_recovery._show_options)

    assert '"m1_rejected_to_m2"' in source
    assert '"message_create"' in source
    assert '"m1_rejected_close"' in source
    assert "Ничего не изменится без отдельного подтверждения" in source
