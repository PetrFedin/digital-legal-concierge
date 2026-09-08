from __future__ import annotations

from app.api.message_center_role_ui_impl import role_safe_message_center_html


_LEGACY_DOCUMENT_ACTION = (
    "localHref('/admin/workdesk/cases/'+caseId+'/action/documents')"
)
_ROLE_SAFE_DOCUMENT_ACTION = "/document-access/review/ui?case_id="


def test_role_safe_message_center_normalizes_all_composed_document_actions() -> None:
    html = role_safe_message_center_html()

    assert _LEGACY_DOCUMENT_ACTION not in html
    assert _ROLE_SAFE_DOCUMENT_ACTION in html
    assert html.count(_ROLE_SAFE_DOCUMENT_ACTION) >= 1


def test_role_safe_message_center_keeps_business_time_contract_after_normalization() -> None:
    html = role_safe_message_center_html()

    assert "const businessTimeZone=" in html
    assert "function formatBusinessTime(value)" in html
    assert "formatBusinessTime(m.created_at)" in html
    assert "businessTimeLabel||businessTimeZone" in html
