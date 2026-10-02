from pathlib import Path
from types import SimpleNamespace

from app.domain.notifications.notification_actions import (
    build_notification_reply_markup,
)


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _callbacks(markup) -> list[str]:
    return [row[0].callback_data for row in markup.inline_keyboard]


def test_scoped_review_api_keeps_case_context_after_queue_becomes_empty():
    source = read("app/api/document_review.py")

    assert "build_document_review_case_context" in source
    assert "if case_id is not None:" in source
    assert "case_context = await build_document_review_case_context(" in source
    assert '"case_context": case_context' in source
    assert '"scoped": case_id is not None' in source


def test_review_workspace_has_one_domain_driven_next_step_and_no_empty_dead_end():
    source = read("app/api/document_review.py")

    assert "Главный следующий шаг" in source
    assert "Комплект документов" in source
    assert "primaryActionMarkup(context)" in source
    assert "accept_m1_case" in source
    assert "wait_client_reupload" in source
    assert "wait_client_submit" in source
    assert "Текущий следующий шаг показан выше" in source
    assert "дело недоступно в вашей роли" in source
    assert "Найти по делу, клиенту, документу" in source
    assert "Очистить поиск" in source


def test_case_acceptance_uses_existing_snapshot_safe_endpoint_and_inline_review():
    source = read("app/api/document_review.py")

    assert "openAcceptForm" in source
    assert "reviewAccept" in source
    assert "Подтвердить принятие" in source
    assert "'/lawyer/cases/'+caseId+'/accept'" in source
    assert "expected_status:context.case_status" in source
    assert "expected_updated_at:context.case_updated_at" in source
    assert "caseDrafts.set(caseDraftKey(),comment)" in source
    assert "caseDrafts.delete(caseDraftKey())" in source
    assert "if(e.status===409)" in source
    assert "Дело изменилось. Черновик сохранён" in source
    assert "Дело принято. Этап договора открыт." in source
    assert "confirm(" not in source


def test_document_decision_is_two_stage_and_preserves_draft_on_conflict():
    source = read("app/api/document_review.py")

    assert "reviewDecision" in source
    assert "data-stage=\"edit\"" in source
    assert "form.dataset.stage!=='review'" in source
    assert "← Изменить комментарий" in source
    assert "drafts.set(draftKey(id,decision),comment)" in source
    assert "Документ изменился. Черновик сохранён" in source
    assert "expected_status:x.status" in source
    assert "expected_version:x.version" in source
    assert "expected_updated_at:x.updated_at" in source
    assert source.index("drafts.delete(draftKey(id,decision))") > source.index(
        "await api('/document-access/review/documents/'"
    )


def test_review_workspace_only_uses_local_navigation_links():
    source = read("app/api/document_review.py")

    assert "x.startsWith('/')&&!x.startsWith('//')" in source
    assert "safeHref(context.message_url" in source
    assert "safeHref(c.workspace_url)" in source


def test_m1_acceptance_notification_opens_real_contract_flow():
    notification = SimpleNamespace(
        recipient_type="client",
        title="client",
        event_code="M1_CASE_ACCEPTED",
        dedupe_key="case:41:lawyer-accept:2026-08-09T12:00:00",
    )

    markup = build_notification_reply_markup(notification)

    assert markup is not None
    assert _callbacks(markup) == ["contract_open", "my_case_open", "message_create"]
    assert all(len(value.encode("utf-8")) <= 64 for value in _callbacks(markup))
    stages = read("app/bot/screens/m1_stages.py")
    assert 'c.data == "contract_open"' in stages
    assert 'callback_matches_action(c.data, "contract_sign")' in stages


def test_m1_acceptance_navigation_is_never_attached_to_staff_notifications():
    notification = SimpleNamespace(
        recipient_type="lawyer",
        title="lawyer",
        event_code="M1_CASE_ACCEPTED",
        dedupe_key="case:41:lawyer-accept:staff-copy",
    )

    assert build_notification_reply_markup(notification) is None
