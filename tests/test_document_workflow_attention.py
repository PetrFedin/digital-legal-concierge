from app.domain.documents.document_workflow import (
    DocumentAttentionState,
    classify_document_attention,
    describe_document_attention,
)
from app.domain.statuses.document_statuses import DocumentStatus


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
