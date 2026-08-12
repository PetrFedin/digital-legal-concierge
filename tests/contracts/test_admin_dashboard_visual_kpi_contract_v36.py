from pathlib import Path


DASHBOARD = Path("app/admin/admin_dashboard.py").read_text(encoding="utf-8")
WORKDESK = Path("app/api/workdesk_ui.py").read_text(encoding="utf-8")


def test_dashboard_exposes_operational_kpis_used_by_workdesk():
    for key in (
        "payment_reviews",
        "refund_pending",
        "client_messages_unread",
        "sla_overdue",
    ):
        assert key in DASHBOARD


def test_workdesk_keeps_four_priority_classes_visible():
    for label in (
        "Платежи требуют сверки",
        "Возвраты ждут обработки",
        "Новые сообщения клиента",
        "SLA-просрочки",
    ):
        assert label in WORKDESK


def test_workdesk_keeps_exact_financial_deep_links():
    assert "/admin/payment-reviews/ui?payment_id=" in WORKDESK
    assert "/admin/refunds/ui?payment_id=" in WORKDESK
