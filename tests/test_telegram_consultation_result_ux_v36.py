from types import SimpleNamespace

from app.bot.screens.consultation_results import _progress_line, _result_buttons
from app.bot.consultation_result import ConsultationResultView
from app.domain.statuses.case_statuses import CaseStatus


def test_terminal_closed_result_is_marked_archive():
    case = SimpleNamespace(status=CaseStatus.M2_CLOSED)
    consultation = SimpleNamespace(status="DONE")

    assert _progress_line(case, consultation) == "АРХИВ · дело закрыто"


def test_active_result_exposes_single_primary_action_and_safe_navigation():
    case = SimpleNamespace(status=CaseStatus.M2_CONSULTATION_DONE)
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


def test_closed_case_has_no_action_that_can_reopen_the_old_consultation():
    case = SimpleNamespace(status=CaseStatus.M2_CLOSED)
    view = ConsultationResultView(
        title="Консультация завершена",
        status_text="Результат готов",
        next_step="Дело закрыто.",
        primary_label="Открыть архив",
        primary_callback="consultation_result_open",
        show_lawyer_result=True,
    )

    assert _result_buttons(view, case=case) == [("🏠 На главную", "nav_home")]
