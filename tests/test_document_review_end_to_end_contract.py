from pathlib import Path

import pytest

from app.domain.documents.document_review_service import (
    DocumentReviewError,
    DocumentReviewService,
)
from app.domain.statuses.document_statuses import DocumentStatus


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_document_review_decisions_validate_client_reason():
    target, comment = DocumentReviewService.validate_decision(
        "approve",
        "Проверено",
    )
    assert target == DocumentStatus.APPROVED
    assert comment == "Проверено"

    with pytest.raises(DocumentReviewError, match="минимум 10 символов"):
        DocumentReviewService.validate_decision("request_reupload", "коротко")
    with pytest.raises(DocumentReviewError, match="допустимое решение"):
        DocumentReviewService.validate_decision("unknown", "Комментарий")


def test_document_review_service_is_locked_scoped_and_idempotent():
    source = read("app/domain/documents/document_review_service.py")

    assert source.count(".with_for_update()") >= 2
    assert "Case.assigned_lawyer_id == actor.lawyer_id" in source
    assert "case.assigned_lawyer_id != actor.lawyer_id" in source
    assert "expected_status" in source
    assert "expected_version" in source
    assert "expected_updated_at" in source
    assert "current_status == target" in source
    assert "ReviewResult(document, case, False, normalized)" in source
    assert source.index("current_status == target") < source.index(
        "self.assert_snapshot("
    )
    assert "DocumentStatus.ON_REVIEW" in source
    assert "DOCUMENT_REVIEW_DECISION" in source


def test_document_review_updates_case_and_notifies_client():
    service = read("app/domain/documents/document_review_service.py")
    rules = read("app/domain/notifications/notification_rules.py")
    templates = read("app/domain/notifications/notification_templates.py")

    assert "LawyerDecisionService" in service
    assert "CaseService" in service
    assert "CaseStatus.M1_DOCS_REQUESTED" in service
    assert "DOCUMENT_APPROVED" in service
    assert "DOCUMENT_REUPLOAD_REQUESTED" in service
    assert "DOCUMENT_REJECTED" in service
    for event in (
        '"DOCUMENT_APPROVED"',
        '"DOCUMENT_REUPLOAD_REQUESTED"',
        '"DOCUMENT_REJECTED"',
    ):
        assert event in rules
    for template in (
        '"document_approved"',
        '"document_reupload_requested"',
        '"document_rejected"',
    ):
        assert template in templates


def test_document_review_api_reuses_personal_document_security_boundary():
    review = read("app/api/document_review.py")
    access = read("app/api/document_access.py")

    assert "resolve_document_actor" in review
    assert '@router.get("/queue")' in review
    assert '@router.post("/documents/{document_id}/decision")' in review
    assert "await db.commit()" in review
    assert "await db.rollback()" in review
    assert "CaseSLAService" in review
    assert "document_review_router" in access
    assert "router.include_router(document_review_router)" in access
    assert "/document-access/documents/" in review
    assert "/grant" in review


def test_document_review_ui_has_complete_recovery_and_stale_protection():
    source = read("app/api/document_review.py")

    assert "Ожидают решения" in source
    assert "Очередь пуста" in source
    assert "Не удалось загрузить очередь" in source
    assert "Повторить" in source
    assert "Принять" in source
    assert "Новая версия" in source
    assert "Отклонить" in source
    assert "expected_status:x.status" in source
    assert "expected_version:x.version" in source
    assert "expected_updated_at:x.updated_at" in source
    assert "const pending=new Set()" in source
    assert "aria-busy" in source
    assert "Решение сохранено, но очередь не обновилась" in source
    assert "window.location.assign(d.download_url)" in source
    assert "file_path" not in source


def test_document_review_center_is_visible_from_operator_workspace():
    source = read("app/api/operator.py")

    assert 'href="/document-access/review/ui"' in source
    assert "Проверка документов" in source
    assert '"document_review": "/document-access/review/ui"' in source
