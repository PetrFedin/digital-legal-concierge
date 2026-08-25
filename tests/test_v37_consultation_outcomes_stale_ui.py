from __future__ import annotations

import inspect

import app.api.consultation_outcomes_product as product
from app.api.consultation_outcomes import OUTCOMES_HTML


def test_product_no_show_api_accepts_exact_visible_slot_snapshot():
    source = inspect.getsource(product.product_mark_lawyer_no_show)

    assert 'payload.get("expected_slot_id")' in source
    assert "expected_slot_id=expected_slot_id" in source
    assert "status_code=409" in source
    assert "await db.rollback()" in source


def test_outcome_ui_preserves_http_409_and_refreshes_authoritative_state():
    html = product._inject_business_timezone_ui(OUTCOMES_HTML)

    assert "e.status=r.status" in html
    assert "Number(e?.status)!==409" in html
    assert "await load()" in html
    assert "Старое действие не применено" in html
    assert "Введённый черновик не удалён" in html


def test_lawyer_no_show_is_bound_to_slot_visible_in_the_card():
    html = product._inject_business_timezone_ui(OUTCOMES_HTML)

    assert "expected_slot_id:row.slot_id" in html
    assert "drafts.delete(draftKey(id,'no_show'))" in html
    assert html.index("await api('/admin/consultation-outcomes/'+id+'/lawyer-no-show'") < html.index(
        "drafts.delete(draftKey(id,'no_show'))"
    )


def test_conflicting_rebook_and_refund_keep_local_drafts_until_success():
    html = product._inject_business_timezone_ui(OUTCOMES_HTML)

    assert "drafts.delete(draftKey(id,'rebook'))" in html
    assert "drafts.delete(draftKey(id,'refund'))" in html
    assert "if(await refreshAfterConflict(e,row))return" in html
    assert "finally" in OUTCOMES_HTML
    assert "pendingConsultations.delete(id)" in OUTCOMES_HTML
