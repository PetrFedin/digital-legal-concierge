from __future__ import annotations

from pathlib import Path

from app.api.payment_review_renderer import render_payment_review_html


def test_payment_review_renderer_composes_guidance_and_history_once() -> None:
    html = render_payment_review_html()

    assert html.count('id="paymentReviewHistory"') == 1
    assert html.count('id="paymentReviewHistoryBody"') == 1
    assert html.count('id="paymentReviewHistoryStatus"') == 1
    assert html.count("async function loadReviewHistory()") == 1
    assert html.count("const paymentReviewBaseLoad=load;") == 1
    assert html.count("await loadReviewHistory();") == 1
    assert html.count("/admin/payment-reviews/'+requestedPaymentId+'/history") == 1
    assert "if(!requestedPaymentId){panel.hidden=true;return}" in html
    assert "terminalCaseId=Number(history.case_id)||terminalCaseId" in html
    assert "Главный следующий шаг" in html
    assert "Вторичные действия" in html
    assert "Комментарии и технические данные журнала здесь не раскрываются" in html


def test_payment_review_history_panel_uses_only_normalized_contract_fields() -> None:
    html = render_payment_review_html()

    for safe_field in (
        "event.kind",
        "event.origin_status",
        "event.resulting_status",
        "event.decision",
        "event.consultation_id",
        "event.slot_id",
        "event.orphan_consultation_id",
        "event.orphan_slot_id",
        "event.actor_type",
        "event.actor_id",
        "event.reason",
        "event.created_at",
    ):
        assert safe_field in html

    for raw_field in (
        "event.comment",
        "event.old_value",
        "event.new_value",
        "event.previous_hash",
        "event.event_hash",
        "event.integrity_key_id",
        "event.reservation_key",
        "event.provider_payload",
    ):
        assert raw_field not in html


def test_payment_review_renderer_is_the_single_final_ui_composition_owner() -> None:
    guard_source = Path("app/api/staff_ui_guards.py").read_text(encoding="utf-8")
    renderer_source = Path("app/api/payment_review_renderer.py").read_text(encoding="utf-8")

    assert "from app.api.payment_review_renderer import render_payment_review_html" in guard_source
    assert "return HTMLResponse(render_payment_review_html())" in guard_source
    assert "PAYMENT_REVIEW_CENTER_HTML" not in guard_source
    assert "_inject_payment_review_guided_copy" not in guard_source
    assert "_PAYMENT_REVIEW_REASON" not in guard_source

    assert "from app.api.payment_review_center import PAYMENT_REVIEW_CENTER_HTML" in renderer_source
    assert "def render_payment_review_html()" in renderer_source
    assert "_inject_guided_copy(PAYMENT_REVIEW_CENTER_HTML)" in renderer_source
    assert "_inject_history_ui(html)" in renderer_source


def test_payment_review_renderer_is_deterministic_and_does_not_mutate_base_template() -> None:
    first = render_payment_review_html()
    second = render_payment_review_html()

    assert first == second
    assert first.count("</body>") == 1
    assert first.count("boot();") == 1
