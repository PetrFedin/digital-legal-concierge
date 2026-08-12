from pathlib import Path

from app.bot.consultation_route_guard import is_consultation_callback


BOT_SOURCE = Path("app/bot/bot.py").read_text(encoding="utf-8")
GUARD_SOURCE = Path("app/bot/consultation_route_guard.py").read_text(encoding="utf-8")
MESSAGES_SOURCE = Path("app/bot/screens/messages.py").read_text(encoding="utf-8")


def test_consultation_entry_callbacks_are_route_guarded():
    assert is_consultation_callback("contact_lawyer") is True
    assert is_consultation_callback("consult_booking_start") is True
    assert is_consultation_callback("consult_slot_select:42") is True
    assert is_consultation_callback("consult_cancel") is True
    assert is_consultation_callback("consultation_result_open") is False


def test_route_guard_is_fail_closed_for_active_m1():
    assert 'RouteCode.M1.value' in GUARD_SOURCE
    assert '"не создаёт консультацию"' in GUARD_SOURCE
    assert '"не резервирует время"' in GUARD_SOURCE
    assert '"не запускает оплату"' in GUARD_SOURCE


def test_route_guard_is_registered_before_telegram_routers():
    middleware_line = 'dispatcher.callback_query.middleware(ConsultationRouteIsolationMiddleware())'
    assert middleware_line in BOT_SOURCE
    assert BOT_SOURCE.index(middleware_line) < BOT_SOURCE.index('for router in [')


def test_terminal_consultation_result_stays_read_only_available():
    assert 'CONSULTATION_READ_ONLY_CALLBACKS' in GUARD_SOURCE
    assert '"consultation_result_open"' in GUARD_SOURCE
    assert 'if value in CONSULTATION_READ_ONLY_CALLBACKS' in GUARD_SOURCE


def test_closed_archive_message_history_does_not_mutate_read_state():
    marker = 'if visible_team_ids and not read_only:'
    assert marker in MESSAGES_SOURCE
    assert 'mark_lawyer_messages_read' in MESSAGES_SOURCE
