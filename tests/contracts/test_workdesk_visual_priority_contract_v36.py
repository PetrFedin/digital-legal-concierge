from pathlib import Path

from app.api.workdesk_ui import WORKDESK_HTML


def test_workdesk_visual_priority_summary_has_operational_kpis():
    required_labels = (
        "Платежи требуют сверки",
        "Возвраты ждут обработки",
        "Новые сообщения клиента",
        "SLA-просрочки",
        "Сделать сейчас",
        "Карточка дела",
    )
    for label in required_labels:
        assert label in WORKDESK_HTML


def test_workdesk_visual_priority_summary_keeps_real_deep_links_and_responsive_layout():
    assert "/admin/payment-reviews/ui" in WORKDESK_HTML
    assert "/admin/refunds/ui" in WORKDESK_HTML
    assert "/message-center/ui" in WORKDESK_HTML
    assert "@media(max-width:720px)" in WORKDESK_HTML
    assert "@media(max-width:460px)" in WORKDESK_HTML


def test_workdesk_visual_source_does_not_create_demo_payment_routes():
    assert "demo-payment" not in WORKDESK_HTML
    assert "/admin/workdesk/fake" not in WORKDESK_HTML
