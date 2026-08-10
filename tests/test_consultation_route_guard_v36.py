from pathlib import Path

from app.bot.consultation_route_guard import (
    CONSULTATION_READ_ONLY_CALLBACKS,
    is_consultation_callback,
)


def test_consultation_mutating_callbacks_are_route_guarded():
    blocked = {
        "consult_booking_start",
        "consult_date:2026-08-20",
        "consult_slot_select:123",
        "consult_pay",
        "consult_cancel",
        "consult_reschedule",
        "consult_reschedule_confirm:1:2:3",
        "consultation_booked_open",
        "consult_subject_start",
    }
    assert all(is_consultation_callback(value) for value in blocked)


def test_terminal_consultation_result_remains_read_only_and_available():
    assert "consultation_result_open" in CONSULTATION_READ_ONLY_CALLBACKS
    assert is_consultation_callback("consultation_result_open") is False


def test_non_consultation_callbacks_are_not_guarded():
    for value in {
        "my_case_open",
        "message_create",
        "payments_open",
        "nav_home",
        "next_action:v2:10:M1_WAITING_PAYMENT_30000",
    }:
        assert is_consultation_callback(value) is False


def test_dispatcher_registers_route_guard_after_draft_guard():
    source = Path("app/bot/bot.py").read_text(encoding="utf-8")
    assert "from app.bot.consultation_route_guard import ConsultationRouteIsolationMiddleware" in source
    draft_index = source.index("dispatcher.callback_query.middleware(DraftProtectionMiddleware())")
    guard_index = source.index("dispatcher.callback_query.middleware(ConsultationRouteIsolationMiddleware())")
    assert draft_index < guard_index


def test_route_guard_does_not_hide_terminal_consultation_result():
    source = Path("app/bot/consultation_route_guard.py").read_text(encoding="utf-8")
    assert "CONSULTATION_READ_ONLY_CALLBACKS" in source
    assert "consultation_result_open" in source
    assert "не запускает оплату" in source
