from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.api.lawyer_workspace import (
    _consultations_today_count,
    _document_readiness,
    _latest_documents,
)
from app.domain.statuses.document_statuses import DocumentStatus


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def document(*, document_type: str, version: int, status: str, title: str):
    return SimpleNamespace(
        id=version,
        document_type=document_type,
        version=version,
        status=status,
        title=title,
    )


def test_latest_document_version_controls_acceptance_readiness():
    documents = [
        document(
            document_type="DDU",
            version=1,
            status=DocumentStatus.APPROVED,
            title="ДДУ",
        ),
        document(
            document_type="DDU",
            version=2,
            status=DocumentStatus.ON_REVIEW,
            title="ДДУ",
        ),
    ]

    latest = _latest_documents(documents)
    ready, reason = _document_readiness(documents)

    assert latest["DDU"].version == 2
    assert ready is False
    assert "ещё не принята" in str(reason)


def test_all_latest_documents_must_be_approved():
    approved = [
        document(
            document_type="DDU",
            version=2,
            status=DocumentStatus.APPROVED,
            title="ДДУ",
        ),
        document(
            document_type="APPENDIX",
            version=1,
            status=DocumentStatus.APPROVED,
            title="Приложение",
        ),
    ]
    unresolved = approved + [
        document(
            document_type="PAYMENT_PROOF",
            version=1,
            status=DocumentStatus.NEEDS_REUPLOAD,
            title="Платёжный документ",
        )
    ]

    assert _document_readiness(approved) == (True, None)
    ready, reason = _document_readiness(unresolved)
    assert ready is False
    assert "Платёжный документ" in str(reason)


def test_consultation_today_metric_counts_only_same_utc_date():
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    values = [
        now - timedelta(hours=1),
        now + timedelta(hours=3),
        now - timedelta(days=1),
        None,
    ]

    assert _consultations_today_count(values, now=now) == 2


def test_lawyer_acceptance_has_domain_document_gate():
    source = read("app/lawyer/lawyer_decisions.py")

    assert "assert_documents_ready_for_acceptance" in source
    assert 'latest_by_type.get("DDU")' in source
    assert "DocumentStatus.APPROVED" in source
    assert "Нельзя принять дело" in source
    assert source.index("await self.assert_documents_ready_for_acceptance") < source.index(
        "next_status=CaseStatus.M1_ACCEPTED"
    )


def test_workspace_is_scoped_to_current_lawyer_and_uses_human_labels():
    source = read("app/api/lawyer_workspace.py")

    assert "require_lawyer_actor" in source
    assert "Case.assigned_lawyer_id == lawyer_id" in source
    assert "Consultation.lawyer_id == lawyer_id" in source
    assert "Consultation.status == ConsultationStatus.BOOKED" in source
    assert "get_client_visible_status" in source
    assert "SLA_LABELS" in source
    assert '@router.get("/data")' in source
    assert '@router.get("/ui"' in source


def test_workspace_prioritizes_documents_sla_and_canonical_consultation_desk():
    source = read("app/api/lawyer_workspace.py")

    assert '"documents_on_review"' in source
    assert '"requires_action"' in source
    assert '"consultations_today"' in source
    assert '"overdue"' in source
    assert "Проверить документы" in source
    assert "Принять дело" in source
    assert "Устранить просрочку" in source
    assert 'href="/document-access/review/ui?case_id=${x.case_id}"' in source
    assert 'href="/lawyer/consultation-desk/ui"' in source
    assert '/message-center/ui?case_id=${x.case_id}' in source


def test_general_workspace_has_no_parallel_consultation_write_surface():
    source = read("app/api/lawyer_workspace.py")

    assert "showTab('consultations'" not in source
    assert "consultationCard(" not in source
    assert "submitConsultation" not in source
    assert "openConsultationForm" not in source
    assert "/lawyer/consultations/" not in source
    assert '"consultations": consultations' not in source
    assert "can_mark_no_show" not in source
    assert "can_complete" not in source
    assert "подготовка, встреча, результат и неявки работают в отдельной очереди" in source


def test_workspace_case_actions_are_single_flight_and_snapshot_protected():
    source = read("app/api/lawyer_workspace.py")

    assert "const pending=new Set()" in source
    assert "aria-busy" in source
    assert "expected_status:x.status" in source
    assert "expected_updated_at:x.updated_at" in source
    assert "/lawyer/cases/" in source
    assert "Операция сохранена, но кабинет не обновился" in source
    assert "Дело принято, но кабинет не обновился" in source
    assert "Операция не выполнена" in source


def test_workspace_preserves_case_comment_drafts_until_success():
    source = read("app/api/lawyer_workspace.py")

    assert "caseDrafts=new Map()" in source
    assert "rememberCaseDraft" in source
    assert "caseDrafts.get(draftKey(id,type))" in source
    assert "caseDrafts.delete(draftKey(id,type))" in source
    assert source.index("caseDrafts.delete(draftKey(id,type))") > source.index(
        "await api(paths[type]"
    )


def test_workspace_has_complete_loading_empty_error_and_recovery_states():
    source = read("app/api/lawyer_workspace.py")

    assert "Загрузка кабинета" in source
    assert "Не удалось загрузить кабинет" in source
    assert "Повторить" in source
    assert "Срочных действий нет" in source
    assert "Активных дел нет" in source
    assert "Отмена" in source
    assert "Открыть консультации →" in source
    assert 'href="/operator"' in source


def test_operator_exposes_new_workspace_and_keeps_legacy_route():
    source = read("app/api/operator.py")

    assert 'href="/lawyer/workspace/ui"' in source
    assert '"lawyer": "/lawyer/workspace/ui"' in source
    assert '"lawyer_legacy": "/lawyer/ui"' in source
    assert "router.include_router(lawyer_workspace_router)" in source
