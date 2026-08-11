from app.api.workdesk_ui import WORKDESK_HTML


def test_workdesk_financial_visual_states_are_distinct():
    assert ".item.financial-review" in WORKDESK_HTML
    assert ".item.financial-refund" in WORKDESK_HTML
    assert ".badge.red" in WORKDESK_HTML
    assert ".badge.amber" in WORKDESK_HTML
    assert "Финансы требуют действия" in WORKDESK_HTML


def test_workdesk_financial_actions_are_exact_payment_deep_links():
    assert "/admin/payment-reviews/ui?payment_id=" in WORKDESK_HTML
    assert "/admin/refunds/ui?payment_id=" in WORKDESK_HTML
    assert "case_id=" in WORKDESK_HTML


def test_workdesk_is_responsive_for_small_screens():
    assert "@media(max-width:720px)" in WORKDESK_HTML
    assert "@media(max-width:460px)" in WORKDESK_HTML
    assert ".metrics,.queues{grid-template-columns:1fr 1fr}" in WORKDESK_HTML
