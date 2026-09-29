from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.lawyer_product import _business_today
from app.domain.statuses.document_statuses import DocumentStatus
from app.lawyer.lawyer_decisions import LawyerDecisionService


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


class _ScalarsResult:
    def __init__(self, documents):
        self._documents = list(documents)

    def scalars(self):
        return self

    def all(self):
        return list(self._documents)


class _DocumentDb:
    def __init__(self, documents):
        self.documents = list(documents)

    async def execute(self, _statement):
        return _ScalarsResult(self.documents)


@pytest.mark.asyncio
async def test_latest_document_version_controls_acceptance_readiness():
    documents = [
        document(
            document_type="DDU",
            version=2,
            status=DocumentStatus.ON_REVIEW,
            title="ДДУ",
        ),
        document(
            document_type="DDU",
            version=1,
            status=DocumentStatus.APPROVED,
            title="ДДУ",
        ),
    ]
    service = LawyerDecisionService(_DocumentDb(documents))

    with pytest.raises(ValueError, match="ещё не принята"):
        await service.assert_documents_ready_for_acceptance(
            case=SimpleNamespace(id=101)
        )


@pytest.mark.asyncio
async def test_all_latest_documents_must_be_approved():
    approved = [
        document(
            document_type="APPENDIX",
            version=1,
            status=DocumentStatus.APPROVED,
            title="Приложение",
        ),
        document(
            document_type="DDU",
            version=2,
            status=DocumentStatus.APPROVED,
            title="ДДУ",
        ),
    ]
    await LawyerDecisionService(_DocumentDb(approved)).assert_documents_ready_for_acceptance(
        case=SimpleNamespace(id=102)
    )

    unresolved = [
        *approved,
        document(
            document_type="PAYMENT_PROOF",
            version=1,
            status=DocumentStatus.NEEDS_REUPLOAD,
            title="Платёжный документ",
        ),
    ]
    with pytest.raises(ValueError, match="Платёжный документ"):
        await LawyerDecisionService(
            _DocumentDb(unresolved)
        ).assert_documents_ready_for_acceptance(case=SimpleNamespace(id=102))


def test_consultation_today_uses_configured_business_date():
    now = datetime(2026, 8, 24, 21, 30, tzinfo=timezone.utc)
    scheduled = datetime(2026, 8, 24, 21, 45, tzinfo=timezone.utc)

    assert _business_today(scheduled, now=now) is True


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


def test_operator_and_product_router_expose_current_and_compatibility_workspaces():
    operator = read("app/api/operator.py")
    product = read("app/api/lawyer_product.py")
    main = read("app/main.py")

    assert 'href="/lawyer/workspace/ui"' in operator
    assert "link('/lawyer/workspace/ui'" in operator
    assert '"/lawyer/ui"' in product
    assert '"/lawyer/workspace/data"' in product
    assert '"/lawyer/workspace/ui"' in product
    assert '("lawyer_product", lawyer_product_router)' in main


def test_lawyer_has_role_scoped_self_contained_case_card():
    source = read("app/api/lawyer_case_card.py")
    workspace = read("app/api/lawyer_workspace.py")
    product = read("app/api/lawyer_product.py")

    assert "lawyer_can_access_case" in source
    assert "require_lawyer_actor" in source
    assert 'audience="lawyer"' in source
    assert '@router.get("/lawyer/cases/{case_id}/workspace")' in source
    assert '@router.get("/lawyer/cases/{case_id}/ui"' in source
    for label in (
        "Клиент",
        "Расчёт",
        "Документы",
        "Оплаты",
        "История процесса",
        "Коммуникации",
        "Главный следующий шаг",
    ):
        assert label in source

    assert '/lawyer/cases/${x.case_id}/ui' in workspace
    assert "router.include_router(lawyer_case_card_router)" in product
