from __future__ import annotations

import re

from app.api.case_action_ui import CASE_ACTION_HTML


def _function_body(name: str) -> str:
    marker = f"async function {name}("
    start = CASE_ACTION_HTML.index(marker)
    next_async = CASE_ACTION_HTML.find("\nasync function ", start + len(marker))
    next_plain = CASE_ACTION_HTML.find("\nfunction ", start + len(marker))
    ends = [value for value in (next_async, next_plain) if value != -1]
    end = min(ends) if ends else len(CASE_ACTION_HTML)
    return CASE_ACTION_HTML[start:end]


def test_exact_case_ui_has_no_browser_prompt_and_uses_inline_drafts():
    assert "prompt(" not in CASE_ACTION_HTML
    assert "Комментарий клиенту" in CASE_ACTION_HTML
    assert "Что произошло" in CASE_ACTION_HTML
    assert "Комментарий к переносу" in CASE_ACTION_HTML
    assert "Причина возврата" in CASE_ACTION_HTML
    assert "Причина и конкретный план" in CASE_ACTION_HTML
    assert CASE_ACTION_HTML.count("Ничего не будет сохранено") >= 4
    assert "Вернуться без сохранения" in CASE_ACTION_HTML


def test_cancel_hides_forms_without_erasing_operator_drafts():
    assert "function closeDocForm(id){document.getElementById('doc_form_'+id)?.classList.remove('open')}" in CASE_ACTION_HTML
    assert "function closeConsultationForm(id,mode){document.getElementById(`consult_form_${id}_${mode}`)?.classList.remove('open')}" in CASE_ACTION_HTML
    assert "function closeSlaForm(){document.getElementById('sla_form')?.classList.remove('open')}" in CASE_ACTION_HTML
    assert ".value=''" not in CASE_ACTION_HTML
    assert ".value = ''" not in CASE_ACTION_HTML


def test_successful_writes_are_not_relabelled_as_failures_when_refresh_breaks():
    assert "async function load(clear=true,announceError=true)" in CASE_ACTION_HTML
    assert "showError(error,announceError);return false" in CASE_ACTION_HTML
    assert "async function refreshAfterWrite(savedMessage)" in CASE_ACTION_HTML
    assert "Изменение сохранено, но экран не обновился" in CASE_ACTION_HTML

    for name in ("submitDoc", "markNoShow", "rebook", "refund", "ackSla"):
        body = _function_body(name)
        write = body.index("await api(")
        write_failure = body.index("catch(error)", write)
        refresh = body.index("await refreshAfterWrite(", write_failure)
        assert write < write_failure < refresh, name
        assert "await load(false)" not in body


def test_consultation_and_sla_show_human_labels_not_internal_codes():
    assert "const consultationStatusLabels=" in CASE_ACTION_HTML
    assert "Встреча завершена — нужен итог" in CASE_ACTION_HTML
    assert "Неявка юриста зафиксирована" in CASE_ACTION_HTML
    assert "const slotStatusLabels=" in CASE_ACTION_HTML
    assert "const slaStatusLabels=" in CASE_ACTION_HTML
    assert "Просрочена первая реакция" in CASE_ACTION_HTML
    assert "Действие просрочено" in CASE_ACTION_HTML
    assert "const paymentStatusLabels=" in CASE_ACTION_HTML
    assert "Возврат поставлен в очередь" in CASE_ACTION_HTML

    assert "Консультация #${x.consultation_id}" not in CASE_ACTION_HTML
    assert "Платёж #${result.payment_id}" not in CASE_ACTION_HTML
    assert "${esc(x.status)}" not in CASE_ACTION_HTML
    assert "${esc(x.slot_status)}" not in CASE_ACTION_HTML
    assert "${esc(item.sla_status)}" not in CASE_ACTION_HTML
    assert "уровень ${esc(item.escalation_level)}" not in CASE_ACTION_HTML


def test_write_confirmations_are_human_and_do_not_expose_internal_ids():
    visible_confirmation_fragments = [
        "Подтвердить неявку юриста?",
        "Подтвердить бесплатный перенос консультации?",
        "Направить оплату консультации в очередь возврата?",
        "Подтвердить план по статусу",
    ]
    for fragment in visible_confirmation_fragments:
        assert fragment in CASE_ACTION_HTML

    assert "по консультации #${id}" not in CASE_ACTION_HTML
    assert "консультацию #${id}" not in CASE_ACTION_HTML
    assert "делу #${id}" not in CASE_ACTION_HTML
    assert "дела #${result.case_id}" not in CASE_ACTION_HTML


def test_exact_case_navigation_and_runtime_scoping_contract_remain_intact():
    assert "/message-center/ui?case_id=${caseId}" in CASE_ACTION_HTML
    assert CASE_ACTION_HTML.count("api('/document-access/review/queue')") == 1
    assert CASE_ACTION_HTML.count("api('/admin/consultation-outcomes'),") == 1
    assert CASE_ACTION_HTML.count("api('/admin/work-queues/consultations')") == 1
    assert CASE_ACTION_HTML.count("api('/admin/sla?overdue_only=false')") == 1

    # Mutation routes stay owned by their domain APIs; the workdesk renderer only
    # swaps read projections for exact-case variants.
    for route in (
        "/document-access/review/documents/",
        "/admin/consultation-outcomes/",
        "/admin/sla/",
    ):
        assert route in CASE_ACTION_HTML


def test_action_forms_keep_internal_ids_only_as_machine_context():
    # Internal identifiers are allowed for data attributes, DOM keys and route
    # calls, but the visible consultation heading is date/client based.
    assert re.search(r'data-consultation-id="\$\{id\}"', CASE_ACTION_HTML)
    assert "<h3>Консультация · ${esc(dt(x.starts_at))}</h3>" in CASE_ACTION_HTML
    assert "Оплата направлена в процесс возврата" in CASE_ACTION_HTML
