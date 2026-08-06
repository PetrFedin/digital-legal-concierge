from pathlib import Path

from app.api.workdesk import _render_case_action_html


ROOT = Path(__file__).resolve().parents[1]


def test_exact_case_action_screen_uses_server_scoped_read_models():
    html = _render_case_action_html(42)

    assert "api('/admin/workdesk/cases/42/documents')" in html
    assert "api('/admin/workdesk/cases/42/consultation-outcomes')," in html
    assert "api('/admin/workdesk/cases/42/consultations-today')" in html
    assert "api('/admin/workdesk/cases/42/sla')" in html

    assert "api('/document-access/review/queue')" not in html
    assert "api('/admin/consultation-outcomes')," not in html
    assert "api('/admin/work-queues/consultations')" not in html
    assert "api('/admin/sla?overdue_only=false')" not in html

    # The screen still writes only through the established domain endpoints.
    assert "/document-access/review/documents/" in html
    assert "/admin/consultation-outcomes/'+id+'/lawyer-no-show" in html
    assert "/admin/consultation-outcomes/'+id+'/rebook" in html
    assert "/admin/consultation-outcomes/'+id+'/refund" in html
    assert "/admin/sla/'+caseId+'/acknowledge" in html


def test_exact_document_screen_handles_every_non_actionable_state_without_dead_end():
    html = _render_case_action_html(42)

    assert "workflow.state==='CLIENT_DRAFT'" in html
    assert "Пакет ещё не передан юристу" in html
    assert "workflow.state==='LEGACY_ATTENTION'" in html
    assert "Статус документов требует уточнения" in html
    assert "workflow.state==='REVIEW'" in html
    assert "Очередь изменилась" in html
    assert "Обновить" in html
    assert "/message-center/ui?case_id=${caseId}" in html
    assert "Вернуться к приоритетам" in html


def test_exact_read_projections_have_case_id_filters_and_actionable_status_only():
    source = (ROOT / "app/api/workdesk.py").read_text(encoding="utf-8")

    assert '@router.get("/admin/workdesk/cases/{case_id}/documents")' in source
    assert (
        '@router.get("/admin/workdesk/cases/{case_id}/consultation-outcomes")'
        in source
    )
    assert (
        '@router.get("/admin/workdesk/cases/{case_id}/consultations-today")'
        in source
    )
    assert '@router.get("/admin/workdesk/cases/{case_id}/sla")' in source

    assert ".where(Case.id == case_id)" in source
    assert ".where(Consultation.case_id == case_id)" in source
    assert "describe_document_attention" in source
    assert "document.status == DocumentStatus.ON_REVIEW" in source
    assert '"workflow": workflow.as_dict()' in source
    assert '"review_started_at"' in source
    scoped_section = source.split(
        '@router.get("/admin/workdesk/cases/{case_id}/documents")', 1
    )[1]
    scoped_section = scoped_section.split(
        '@router.get("/admin/workdesk/ui"', 1
    )[0]
    assert ".limit(300)" not in scoped_section
    assert ".limit(500)" not in scoped_section


def test_legacy_admin_document_queue_accepts_only_submitted_packages():
    source = (ROOT / "app/api/web_admin.py").read_text(encoding="utf-8")

    assert "DOCUMENT_REVIEW_STATUSES = ACTIONABLE_REVIEW_STATUSES" in source
    assert "Document.status.in_(DOCUMENT_REVIEW_STATUSES)" in source
    assert "Только пакеты, которые клиент передал юристу" in source
    assert "Ожидает передачи юристу" in source


def test_case_action_template_contract_fails_closed_on_drift(monkeypatch):
    import app.api.workdesk as workdesk

    monkeypatch.setattr(workdesk, "CASE_ACTION_HTML", "template without fetches")

    try:
        workdesk._render_case_action_html(7)
    except RuntimeError as error:
        assert "template contract changed" in str(error)
    else:
        raise AssertionError("Template drift must not silently restore global queues")
