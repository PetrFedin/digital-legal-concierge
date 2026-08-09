from __future__ import annotations

import inspect
from pathlib import Path

from app.bot.bot import build_dispatcher
from app.bot.screens.consultation_booking_ui import (
    consultation_primary_action,
    consultation_status_label,
    normalized_consultation_status,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus


ROOT = Path(__file__).resolve().parents[1]


def test_unknown_consultation_status_degrades_to_safe_label_instead_of_raising():
    assert normalized_consultation_status("FUTURE_STATUS") is None
    assert normalized_consultation_status(None) is None
    assert consultation_status_label("FUTURE_STATUS") == "Статус уточняется"


def test_booked_consultation_prioritizes_preparation_not_hidden_mutation():
    primary, next_step = consultation_primary_action(
        status=ConsultationStatus.BOOKED,
        description_ready=True,
        active_document_count=0,
    )

    assert primary == ("📄 Добавить документы", "documents_open")
    assert "Консультация назначена" in next_step
    assert "consult_reschedule" not in primary
    assert "consult_cancel" not in primary

    with_documents, prepared_next = consultation_primary_action(
        status=ConsultationStatus.BOOKED,
        description_ready=True,
        active_document_count=3,
    )
    assert with_documents == ("📄 Проверить документы", "documents_open")
    assert "дождитесь времени встречи" in prepared_next


def test_missing_description_always_precedes_slot_or_payment_actions():
    for status in [
        ConsultationStatus.DESCRIPTION_PENDING,
        ConsultationStatus.SLOT_PENDING,
        ConsultationStatus.PAYMENT_PENDING,
        ConsultationStatus.BOOKED,
    ]:
        primary, next_step = consultation_primary_action(
            status=status,
            description_ready=False,
            active_document_count=0,
        )
        assert primary == ("▶️ Описать вопрос", "consult_subject_start")
        assert "После этого станет доступен выбор времени" in next_step


def test_terminal_status_recovers_to_result_screen():
    for status in [
        ConsultationStatus.DONE,
        ConsultationStatus.CLIENT_NO_SHOW,
        ConsultationStatus.LAWYER_NO_SHOW,
        ConsultationStatus.CANCELLED,
        ConsultationStatus.CLOSED,
    ]:
        primary, next_step = consultation_primary_action(
            status=status,
            description_ready=True,
            active_document_count=1,
        )
        assert primary == (
            "👨‍⚖ Открыть итог консультации",
            "consultation_result_open",
        )
        assert "Откройте итог юриста" in next_step


def test_unknown_non_terminal_status_recovers_to_slot_selection():
    primary, next_step = consultation_primary_action(
        status=None,
        description_ready=True,
        active_document_count=0,
    )
    assert primary == ("▶️ Выбрать дату и время", "consult_booking_start")
    assert "Выберите свободную дату и время" in next_step


def test_action_center_has_visual_hierarchy_and_explicit_booking_controls():
    source = (ROOT / "app/bot/screens/consultation_booking_ui.py").read_text(
        encoding="utf-8"
    )

    assert "👨‍⚖ КОНСУЛЬТАЦИЯ" in source
    assert '"СЕЙЧАС\\n"' in source
    assert '"ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\\n"' in source
    assert '"ПОДГОТОВКА\\n"' in source
    assert "Первая кнопка ниже — самое актуальное безопасное действие" in source
    assert '("🔄 Перенести консультацию", "consult_reschedule")' in source
    assert '("Отменить консультацию", "consult_cancel")' in source
    assert "ConsultationStatus(str(value))" in source
    assert "except (TypeError, ValueError)" in source


def test_result_router_precedes_action_center_and_action_center_precedes_legacy_handler():
    source = inspect.getsource(build_dispatcher)

    result = source.index("consultation_results.router")
    action_center = source.index("consultation_booking_ui.router")
    description = source.index("consultation_description.router")
    legacy_intake = source.index("consultation_intake.router")

    assert result < action_center < description < legacy_intake
