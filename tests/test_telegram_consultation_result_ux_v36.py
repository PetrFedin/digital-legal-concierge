from pathlib import Path
from types import SimpleNamespace

from app.bot.screens.consultation_results import _progress_line, _result_buttons
from app.bot.consultation_result import ConsultationResultView
from app.domain.statuses.case_statuses import CaseStatus


def test_terminal_closed_result_is_marked_archive():
    case = SimpleNamespace(status=CaseStatus.M2_CLOSED)
    consultation = SimpleNamespace(status="DONE")

    assert _progress_line(case, consultation) == "АРХИВ · дело закрыто"


def test_active_result_exposes_single_primary_action_and_safe_navigation():
    case = SimpleNamespace(id=41, status=CaseStatus.M2_CONSULTATION_DONE)
    view = ConsultationResultView(
        title="Консультация завершена",
        status_text="Результат готов",
        next_step="Откройте итог и выберите следующее действие.",
        primary_label="👨‍⚖ Итог консультации",
        primary_callback="consultation_result_open",
        show_lawyer_result=True,
    )

    buttons = _result_buttons(view, case=case)
    assert buttons[0] == ("👨‍⚖ Итог консультации", "consultation_result_open")
    assert ("📁 Моё дело", "my_case_open") in buttons
    assert ("🏠 Главная", "nav_home") in buttons


def test_follow_up_result_binds_primary_mutation_to_exact_case():
    case = SimpleNamespace(id=41, status=CaseStatus.M2_CONSULTATION_DONE)
    view = ConsultationResultView(
        title="Консультация завершена",
        status_text="Рекомендована повторная встреча",
        next_step="Запишитесь повторно.",
        primary_label="📅 Записаться повторно",
        primary_callback="consult_follow_up_start",
        show_lawyer_result=True,
    )

    buttons = _result_buttons(view, case=case)
    assert buttons[0] == ("📅 Записаться повторно", "consult_follow_up_start:v2:41")


def test_follow_up_handler_is_case_scoped_and_books_same_case():
    source = Path("app/bot/screens/consultation_results.py").read_text(encoding="utf-8")

    assert 'callback_matches_action(c.data, "consult_follow_up_start")' in source
    handler = source.split("async def consult_follow_up_start(callback: CallbackQuery, db):", 1)[1]
    assert "resolve_case_callback_scope(" in handler
    assert 'action="consult_follow_up_start"' in handler
    assert "allow_legacy_message_case_context=True" in handler
    assert 'bound_case_callback("consult_follow_up_start", case_id)' in handler
    assert 'bound_case_callback("consult_booking_start", case_id)' in handler
    assert handler.index("description = clip_client_result(follow_up.client_description") < handler.index(
        "await db.commit()"
    )


def test_result_meeting_time_uses_shared_business_timezone_formatter():
    source = Path("app/bot/screens/consultation_results.py").read_text(encoding="utf-8")

    assert "from app.presentation_time import format_business_datetime" in source
    formatter = source.split("def _format_scheduled_at", 1)[1].split(
        "def _result_buttons", 1
    )[0]
    assert "format_business_datetime(consultation.scheduled_at)" in formatter
    assert ".strftime(" not in formatter


def test_closed_case_exposes_read_only_archive_without_stale_consultation_actions():
    case = SimpleNamespace(id=41, status=CaseStatus.M2_CLOSED)
    view = ConsultationResultView(
        title="Консультация завершена",
        status_text="Результат готов",
        next_step="Дело закрыто.",
        primary_label="Записаться повторно",
        primary_callback="consult_follow_up_start",
        show_lawyer_result=True,
    )

    buttons = _result_buttons(view, case=case)
    assert buttons == [
        ("📁 Архив обращения", "my_case_open"),
        ("📄 Документы", "documents_open"),
        ("💳 Оплаты", "payments_open"),
        ("🕘 История", "case_history_open"),
        ("🏠 Главная", "nav_home"),
    ]
    callbacks = {callback for _label, callback in buttons}
    assert "consult_follow_up_start" not in callbacks
    assert "consultation_booked_open" not in callbacks
    assert "message_create" not in callbacks
