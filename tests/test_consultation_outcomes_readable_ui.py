from __future__ import annotations

import inspect

from app.api.consultation_outcomes import (
    OUTCOMES_HTML,
    _consultation_status_label,
    _slot_status_label,
    available_slots,
    list_outcome_queue,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus


def _function(name: str) -> str:
    marker = f"async function {name}("
    start = OUTCOMES_HTML.index(marker)
    end = OUTCOMES_HTML.find("\nasync function ", start + len(marker))
    if end < 0:
        end = OUTCOMES_HTML.index("\nboot();", start)
    return OUTCOMES_HTML[start:end]


def test_queue_projects_human_statuses_without_removing_machine_state():
    source = inspect.getsource(list_outcome_queue)

    assert '"status": consultation.status' in source
    assert '"status_label": _consultation_status_label(consultation.status)' in source
    assert '"slot_status": slot.status' in source
    assert '"slot_status_label": _slot_status_label(slot.status)' in source
    assert _consultation_status_label(ConsultationStatus.BOOKED) == "Ожидает результата встречи"
    assert _consultation_status_label(ConsultationStatus.LAWYER_NO_SHOW) == "Неявка юриста зафиксирована"
    assert _slot_status_label("booked") == "Забронирован"
    assert _slot_status_label("completed") == "Встреча завершена"


def test_available_slot_fallback_does_not_expose_internal_lawyer_id():
    source = inspect.getsource(available_slots)

    assert 'else "Юрист не указан"' in source
    assert 'f"Юрист #{slot.lawyer_id}"' not in source


def test_outcome_center_uses_cards_inline_forms_and_exact_case_navigation():
    assert "<table" not in OUTCOMES_HTML
    assert "prompt(" not in OUTCOMES_HTML
    assert "Проверка перед фиксацией неявки" in OUTCOMES_HTML
    assert "Бесплатный перенос" in OUTCOMES_HTML
    assert "Маршрут возврата" in OUTCOMES_HTML
    assert OUTCOMES_HTML.count("Вернуться без сохранения") >= 3
    assert OUTCOMES_HTML.count("Ничего не изменится") >= 3
    assert "/admin/workdesk/cases/${caseId}/action/consultation" in OUTCOMES_HTML
    assert "/message-center/ui?case_id=${caseId}" in OUTCOMES_HTML
    assert 'href="/admin-ui"' not in OUTCOMES_HTML


def test_visible_work_context_has_no_raw_telegram_or_internal_result_ids():
    assert "TG ${esc(x.client_telegram_id)}" not in OUTCOMES_HTML
    assert "Консультация #${" not in OUTCOMES_HTML
    assert "Платёж #${" not in OUTCOMES_HTML
    assert "по консультации #${" not in OUTCOMES_HTML
    assert "${esc(x.status)}" not in OUTCOMES_HTML
    assert "${esc(x.slot_status)}" not in OUTCOMES_HTML
    assert "consultationLabel(x)" in OUTCOMES_HTML
    assert "slotLabel(x)" in OUTCOMES_HTML
    assert "row.case_number" in OUTCOMES_HTML


def test_drafts_and_selected_slot_survive_cancel_and_rerender_until_success():
    assert "drafts=new Map()" in OUTCOMES_HTML
    assert "selectedSlots=new Map()" in OUTCOMES_HTML
    assert "rememberDraft" in OUTCOMES_HTML
    assert "rememberSlot" in OUTCOMES_HTML
    assert "function closeForm(id,mode){document.getElementById(`form_${id}_${mode}`)?.classList.remove('open')}" in OUTCOMES_HTML
    assert ".value=''" not in OUTCOMES_HTML
    assert ".value = ''" not in OUTCOMES_HTML
    assert "drafts.delete(draftKey(id,'no_show'))" in OUTCOMES_HTML
    assert "drafts.delete(draftKey(id,'rebook'))" in OUTCOMES_HTML
    assert "drafts.delete(draftKey(id,'refund'))" in OUTCOMES_HTML
    assert "selectedSlots.delete(Number(id))" in OUTCOMES_HTML


def test_write_success_is_not_relabelled_as_failure_when_refresh_breaks():
    for name, saved_marker in (
        ("markNoShow", "Неявка сохранена, но список не обновился"),
        ("rebook", "Перенос сохранён, но список не обновился"),
        ("refund", "Направление на возврат сохранено, но список не обновился"),
    ):
        body = _function(name)
        write = body.index("await api(")
        refresh = body.index("try{await load()}", write)
        refresh_failure = body.index("catch(e)", refresh)
        write_failure = body.index("catch(e)", refresh_failure + len("catch(e)"))
        assert write < refresh < refresh_failure < write_failure, name
        assert saved_marker in body
        assert "Черновик остаётся на экране" in body


def test_no_show_timing_explanation_matches_domain_policy():
    source = inspect.getsource(list_outcome_queue)

    assert "timedelta(minutes=15)" in source
    assert "ConsultationSlot.starts_at <= cutoff" in source
    assert "через 15 минут после начала" in OUTCOMES_HTML
