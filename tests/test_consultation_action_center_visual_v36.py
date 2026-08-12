from app.bot.screens.consultation_booking_ui import (
    consultation_progress,
    consultation_progress_bar,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus


def test_consultation_progress_is_status_driven():
    assert consultation_progress(
        status=ConsultationStatus.DESCRIPTION_PENDING,
        description_ready=False,
        active_document_count=0,
    )[0] == 0
    assert consultation_progress(
        status=ConsultationStatus.SLOT_PENDING,
        description_ready=True,
        active_document_count=1,
    )[0] == 1
    assert consultation_progress(
        status=ConsultationStatus.PAYMENT_PENDING,
        description_ready=True,
        active_document_count=1,
    )[0] == 2
    assert consultation_progress(
        status=ConsultationStatus.BOOKED,
        description_ready=True,
        active_document_count=1,
    )[0] == 3
    assert consultation_progress(
        status=ConsultationStatus.DONE,
        description_ready=True,
        active_document_count=1,
    )[0] == 4


def test_progress_bar_has_five_stages_and_terminal_state():
    bar = consultation_progress_bar(3)
    assert bar.count("●") == 4
    assert bar.count("○") == 1
    assert "Вопрос" in bar
    assert "Подтверждение" in bar
    assert "Готово" in bar
