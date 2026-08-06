from datetime import datetime, timezone
from types import SimpleNamespace

from app.bot.client_case_view import _document_overview, _priority_action
from app.domain.documents.document_workflow import (
    DocumentAttentionState,
    classify_document_attention,
    describe_document_attention,
)
from app.domain.statuses.document_statuses import DocumentStatus


def _client_document(status: str, *, document_id: int = 1):
    return SimpleNamespace(
        id=document_id,
        status=status,
        title="ДДУ",
        version=1,
        lawyer_comment=None,
        updated_at=datetime(2026, 8, 6, 10, tzinfo=timezone.utc),
    )


def test_uploaded_files_are_client_drafts_not_lawyer_review_work():
    descriptor = describe_document_attention([DocumentStatus.UPLOADED])

    assert descriptor.state == DocumentAttentionState.CLIENT_DRAFT
    assert descriptor.code == "document_draft"
    assert descriptor.actionable is False
    assert descriptor.primary_href_kind == "message"
    assert "не передал" in descriptor.label.lower()


def test_on_review_files_are_actionable_for_the_lawyer():
    descriptor = describe_document_attention([DocumentStatus.ON_REVIEW])

    assert descriptor.state == DocumentAttentionState.REVIEW
    assert descriptor.code == "documents"
    assert descriptor.actionable is True
    assert descriptor.primary_href_kind == "review"
    assert descriptor.primary_label == "Проверить документы"


def test_actionable_review_wins_over_client_drafts_in_mixed_package():
    state = classify_document_attention(
        [DocumentStatus.UPLOADED, DocumentStatus.ON_REVIEW]
    )

    assert state == DocumentAttentionState.REVIEW


def test_legacy_attention_never_exposes_review_decisions():
    descriptor = describe_document_attention(["PENDING_REVIEW"])

    assert descriptor.state == DocumentAttentionState.LEGACY_ATTENTION
    assert descriptor.code == "document_legacy"
    assert descriptor.actionable is False
    assert descriptor.primary_href_kind == "message"
    assert "уточнения" in descriptor.label.lower()


def test_client_case_overview_does_not_call_legacy_status_lawyer_review():
    overview = _document_overview([_client_document("PENDING_REVIEW")])
    action = _priority_action(
        SimpleNamespace(status="M1_LAWYER_REVIEW"),
        overview,
    )

    assert overview.review_count == 0
    assert overview.legacy_attention_count == 1
    assert "статус уточняется" in overview.summary.lower()
    assert action is not None
    assert action.label == "Уточнить статус документов"
    assert action.callback == "message_create"


def test_client_case_mixed_review_and_legacy_state_keeps_honest_waiting_state():
    overview = _document_overview(
        [
            _client_document("ON_REVIEW", document_id=1),
            _client_document("PENDING_REVIEW", document_id=2),
        ]
    )
    action = _priority_action(
        SimpleNamespace(status="M1_LAWYER_REVIEW"),
        overview,
    )

    assert overview.review_count == 1
    assert overview.legacy_attention_count == 1
    assert "проверяет юрист" in overview.summary.lower()
    assert "статус уточняется" in overview.summary.lower()
    assert action is None


def test_completed_and_empty_documents_do_not_create_false_attention():
    assert classify_document_attention([]) == DocumentAttentionState.NONE
    assert (
        classify_document_attention(
            [DocumentStatus.APPROVED, DocumentStatus.ARCHIVED]
        )
        == DocumentAttentionState.NONE
    )


def test_classifier_normalizes_string_and_enum_values():
    assert classify_document_attention([" on_review "]) == DocumentAttentionState.REVIEW
    assert (
        classify_document_attention([DocumentStatus.UPLOADED.value])
        == DocumentAttentionState.CLIENT_DRAFT
    )
