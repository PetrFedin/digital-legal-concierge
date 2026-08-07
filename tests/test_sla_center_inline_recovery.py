from __future__ import annotations

import inspect

from app.api.sla_center import (
    SLA_CENTER_HTML,
    _escalation_label,
    _route_label,
    _sla_label,
    list_sla_cases,
)


def _async_function(name: str) -> str:
    marker = f"async function {name}("
    start = SLA_CENTER_HTML.index(marker)
    next_async = SLA_CENTER_HTML.find("\nasync function ", start + len(marker))
    next_plain = SLA_CENTER_HTML.find("\nfunction ", start + len(marker))
    ends = [value for value in (next_async, next_plain) if value != -1]
    end = min(ends) if ends else len(SLA_CENTER_HTML)
    return SLA_CENTER_HTML[start:end]


def test_sla_projection_exposes_human_labels_without_breaking_snapshot_fields():
    source = inspect.getsource(list_sla_cases)

    assert '"route_label": _route_label(case.route)' in source
    assert '"case_status_label": get_client_visible_status(case.status)' in source
    assert '"sla_label": _sla_label(case.sla_status)' in source
    assert '"escalation_label": _escalation_label(case.escalation_level)' in source
    assert '"sla_status": case.sla_status' in source
    assert '"escalation_level": int(case.escalation_level or 0)' in source

    assert _route_label("M1") == "Ведение дела"
    assert _route_label("M2") == "Консультация"
    assert _sla_label("ACTION_OVERDUE") == "Действие просрочено"
    assert _escalation_label(0) == "Обычный контроль"
    assert _escalation_label(1) == "Требует повышенного внимания"
    assert _escalation_label(2) == "Высокий приоритет"
    assert _escalation_label(9) == "Критический приоритет"


def test_sla_center_replaces_browser_prompt_with_persistent_inline_plan():
    assert "prompt(" not in SLA_CENTER_HTML
    assert "План устранения просрочки" in SLA_CENTER_HTML
    assert "Причина просрочки, что делаем сейчас" in SLA_CENTER_HTML
    assert "Ничего не изменится" in SLA_CENTER_HTML
    assert "Подтвердить план" in SLA_CENTER_HTML
    assert "Вернуться без сохранения" in SLA_CENTER_HTML
    assert "const pendingCases=new Set(),draftPlans=new Map()" in SLA_CENTER_HTML
    assert "function rememberDraft(id,value){draftPlans.set(Number(id),value)}" in SLA_CENTER_HTML
    assert "function closePlan(id){document.getElementById('sla_plan_'+id)?.classList.remove('open')}" in SLA_CENTER_HTML
    assert "draftPlans.delete(Number(id))" in SLA_CENTER_HTML
    assert ".value=''" not in SLA_CENTER_HTML
    assert ".value = ''" not in SLA_CENTER_HTML


def test_sla_center_uses_case_number_and_human_statuses_in_visible_ui():
    assert "x.case_number" in SLA_CENTER_HTML
    assert "x.route_label" in SLA_CENTER_HTML
    assert "x.case_status_label" in SLA_CENTER_HTML
    assert "x.sla_label" in SLA_CENTER_HTML
    assert "x.escalation_label" in SLA_CENTER_HTML
    assert "const slaLabels=" in SLA_CENTER_HTML
    assert "Просрочена первая реакция" in SLA_CENTER_HTML
    assert "Действие просрочено" in SLA_CENTER_HTML

    assert "${esc(x.sla_status)}" not in SLA_CENTER_HTML
    assert "${esc(x.escalation_level)}" not in SLA_CENTER_HTML
    assert "TG ${esc(x.client_telegram_id)}" not in SLA_CENTER_HTML
    assert "по делу #${id}" not in SLA_CENTER_HTML
    assert "SLA дела #${result.case_id}" not in SLA_CENTER_HTML
    assert "${expectedStatus}" not in SLA_CENTER_HTML
    assert "${expectedLevel}" not in SLA_CENTER_HTML


def test_sla_ack_write_and_refresh_outcomes_are_separated():
    body = _async_function("ack")
    write = body.index("result=await api('/admin/sla/'+id+'/acknowledge")
    write_failure = body.index("catch(e)", write)
    refresh = body.index("await refreshAfter(", write_failure)

    assert write < write_failure < refresh
    assert "Черновик остаётся на экране" in body
    assert "await load(" not in body

    refresh_body = _async_function("refreshAfter")
    assert "load(currentOverdueOnly,null,false)" in refresh_body
    assert "Изменение сохранено, но список не обновился" in refresh_body
    assert "Нажмите «Повторить»" in refresh_body


def test_sla_center_has_no_dead_end_on_empty_or_load_failure():
    assert "В выбранной категории дел нет" in SLA_CENTER_HTML
    assert "Показать все активные SLA" in SLA_CENTER_HTML
    assert "Рабочий стол" in SLA_CENTER_HTML
    assert "Список SLA не загружен" in SLA_CENTER_HTML
    assert "Повторить" in SLA_CENTER_HTML
    assert "/admin/workdesk/ui" in SLA_CENTER_HTML
    assert "/message-center/ui?case_id=${id}" in SLA_CENTER_HTML
    assert "/admin/workdesk/cases/${id}/action/sla" in SLA_CENTER_HTML
    assert 'href="/admin-ui"' not in SLA_CENTER_HTML


def test_global_sla_check_preserves_success_when_refresh_fails():
    body = _async_function("runCheck")
    write = body.index("result=await api('/admin/sla/run'")
    write_failure = body.index("catch(e)", write)
    refresh = body.index("await refreshAfter(", write_failure)

    assert write < write_failure < refresh
    assert "Новых эскалаций" in body
    assert "await load(" not in body
