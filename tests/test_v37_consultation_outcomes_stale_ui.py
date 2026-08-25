from __future__ import annotations

import inspect

import app.api.consultation_outcomes_product as product
from app.api.consultation_outcomes import OUTCOMES_HTML
from app.api.guided_consultation_outcomes import _inject_client_no_show_ui
from app.api.legacy_consultation_outcome_guard import inject_legacy_outcome_ui


def _canonical_outcomes_html() -> str:
    html = _inject_client_no_show_ui(OUTCOMES_HTML)
    html = inject_legacy_outcome_ui(html)
    return product._inject_business_timezone_ui(html)


def test_product_no_show_api_accepts_exact_visible_slot_snapshot():
    source = inspect.getsource(product.product_mark_lawyer_no_show)

    assert 'payload.get("expected_slot_id")' in source
    assert "expected_slot_id=expected_slot_id" in source
    assert "status_code=409" in source
    assert "await db.rollback()" in source


def test_outcome_ui_preserves_http_409_and_refreshes_authoritative_state():
    html = _canonical_outcomes_html()

    assert "e.status=r.status" in html
    assert "Number(e?.status)!==409" in html
    assert "await load()" in html
    assert "Старое действие не применено" in html
    assert "Введённый черновик не удалён" in html


def test_lawyer_no_show_is_bound_to_slot_visible_in_the_card():
    html = _canonical_outcomes_html()

    assert "expected_slot_id:row.slot_id" in html
    assert "drafts.delete(draftKey(id,'no_show'))" in html
    assert html.index("await api('/admin/consultation-outcomes/'+id+'/lawyer-no-show'") < html.index(
        "drafts.delete(draftKey(id,'no_show'))"
    )


def test_conflicting_rebook_and_refund_keep_local_drafts_until_success():
    html = _canonical_outcomes_html()

    assert "drafts.delete(draftKey(id,'rebook'))" in html
    assert "drafts.delete(draftKey(id,'refund'))" in html
    assert "if(await refreshAfterConflict(e,row))return" in html
    assert "finally" in OUTCOMES_HTML
    assert "pendingConsultations.delete(id)" in OUTCOMES_HTML


def test_client_no_show_final_handlers_replace_broken_reload_without_button():
    html = _canonical_outcomes_html()
    product_patch = html[html.rfind("async function clientNoShowProductAction") :]

    assert "window.clientNoShowRebook" in product_patch
    assert "window.clientNoShowClose" in product_patch
    assert "rememberDraft(id,mode,comment)" in product_patch
    assert "if(await refreshAfterConflict(e,row))return" in product_patch
    assert "drafts.delete(draftKey(id,mode))" in product_patch
    assert "try{await load()}" in product_patch
    assert "await reload()" not in product_patch


def test_client_no_show_draft_is_deleted_only_after_successful_api_call():
    html = _canonical_outcomes_html()
    start = html.rfind("async function clientNoShowProductAction")
    end = html.find("window.clientNoShowRebook", start)
    handler = html[start:end]

    api_index = handler.index("await api(path")
    delete_index = handler.index("drafts.delete(draftKey(id,mode))")
    conflict_index = handler.index("if(await refreshAfterConflict(e,row))return")

    assert api_index < delete_index < conflict_index
    assert "Черновик остаётся на экране" in handler
