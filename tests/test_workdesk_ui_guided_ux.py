from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workdesk_exposes_search_focus_and_closeable_case_context():
    source = read("app/api/workdesk_ui.py")

    assert 'id="filter"' in source
    assert 'aria-label="Поиск по текущему списку"' in source
    assert "function applyFilter()" in source
    assert "function clearFilter()" in source
    assert 'data-case="${x.id}"' in source
    assert "function markSelected(id)" in source
    assert "function closeCase()" in source
    assert 'id="closeCase"' in source
    assert "Дело не выбрано" in source
    assert "Ничего не найдено" in source


def test_workdesk_empty_and_error_states_always_offer_recovery():
    source = read("app/api/workdesk_ui.py")

    assert "Не удалось загрузить данные" in source
    assert "Повторить" in source
    assert "К приоритетам" in source
    assert "Очередь пуста" in source
    assert 'href="/admin/workdesk/ui"' in source
    assert "Карточка не загружена" in source
    assert "Повторить карточку" in source
    assert "closeCase()" in source


def test_workdesk_case_card_keeps_exact_safe_workflows_after_visual_polish():
    source = read("app/api/workdesk_ui.py")

    assert "Сделать сейчас" in source
    assert "Рабочие действия" in source
    assert "Контакт клиента" in source
    assert "История дела" in source
    assert "Документы" in source
    assert "'/admin/cases/'+id+'/auto-assign'" in source
    assert "/message-center/ui?case_id=${id}" in source
    assert "/admin/workdesk/cases/${id}/action/documents" in source
    assert "/admin/workdesk/cases/${id}/action/consultation" in source
    assert "/admin/workdesk/cases/${id}/action/sla" in source
    assert "/advance" not in source
    assert "force=True" not in source


def test_workdesk_case_context_is_route_aware_for_self_filing_and_m2():
    source = read("app/api/workdesk_ui.py")

    assert "function caseControlCells(d)" in source
    assert "function caseWorkflowLinks(id,d)" in source
    assert "x.self_filing_sla_due_at" in source
    assert "Пакет выдать до" in source
    assert "Срок пакета" in source
    assert "Email-доставка" in source
    assert "Пакет самостоятельной подачи" in source
    assert "/self-filing/ui?case_id=" in source
    assert "if(x.route==='M2')" in source
    assert "Рабочие действия" in source
    assert "/admin/cases/${id}/ui" in source


def test_workdesk_self_filing_card_does_not_offer_m2_consultation_or_fake_sla_assignment():
    source = read("app/api/workdesk_ui.py")
    links = source.split("function caseWorkflowLinks(id,d)", 1)[1].split(
        "function financialAttention", 1
    )[0]

    self_filing_branch = links.split("if(sf)return", 1)[1].split(
        "if(x.route==='M2')", 1
    )[0]
    assert "/self-filing/ui?case_id=" in self_filing_branch
    assert "/action/consultation" not in self_filing_branch
    assert "Назначить перед SLA" not in self_filing_branch
