from app.api.workdesk_ui import WORKDESK_HTML


def test_overdue_queue_assigns_lawyer_before_opening_sla_for_unassigned_case():
    assert "queue==='overdue'&&!x.lawyer_id" in WORKDESK_HTML
    assert "Назначить юриста для устранения SLA" in WORKDESK_HTML


def test_case_card_does_not_offer_dead_sla_link_before_assignment():
    assert "slaShortcut=d.case.lawyer_id?" in WORKDESK_HTML
    assert "Назначить перед SLA" in WORKDESK_HTML
