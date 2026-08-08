from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.bot.client_case_view import (
    CLIENT_ACTIONS,
    ClientAction,
    _action_key,
    _document_overview,
    _priority_action,
    progress_bar,
)
from app.domain.statuses.case_statuses import CaseStatus


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def document(
    status: str,
    *,
    version: int = 1,
    comment: str | None = None,
    updated_at: datetime | None = None,
):
    return SimpleNamespace(
        id=version,
        title="ДДУ",
        version=version,
        status=status,
        lawyer_comment=comment,
        updated_at=updated_at or datetime(2026, 8, 5, version, tzinfo=timezone.utc),
    )


def case(status: str = CaseStatus.M1_DOCUMENTS_PENDING):
    return SimpleNamespace(
        id=17,
        status=status,
        updated_at=datetime(2026, 8, 5, 10, tzinfo=timezone.utc),
    )


def test_archived_versions_do_not_inflate_current_document_readiness():
    overview = _document_overview(
        [
            document("APPROVED", version=3),
            document("ARCHIVED", version=2),
            document("ARCHIVED", version=1),
        ]
    )

    assert overview.current_count == 1
    assert overview.archived_count == 2
    assert overview.approved_count == 1
    assert overview.summary == "1 актуальных · все приняты"


def test_reupload_reason_overrides_case_status_action():
    overview = _document_overview(
        [
            document(
                "NEEDS_REUPLOAD",
                comment="Добавьте подписанную последнюю страницу",
            )
        ]
    )

    action = _priority_action(case(CaseStatus.M1_LAWYER_REVIEW), overview)

    assert action == ClientAction(
        "Загрузить новую версию",
        "documents_open",
        "Загрузите исправленную версию файла по замечанию юриста.",
    )
    assert overview.blocker == "ДДУ: Добавьте подписанную последнюю страницу"


def test_uploaded_documents_create_real_submission_action():
    overview = _document_overview([document("UPLOADED")])

    action = _priority_action(case(), overview)

    assert action is not None
    assert action.label == "Передать документы юристу"
    assert action.callback == "doc_finish_upload"
    assert "готовы к передаче" in overview.summary


def test_reviewed_documents_remove_false_client_action():
    overview = _document_overview([document("ON_REVIEW")])

    action = _priority_action(case(CaseStatus.M1_LAWYER_REVIEW), overview)

    assert action is None
    assert overview.summary == "1 актуальных · 1 проверяет юрист"


def test_action_snapshot_changes_when_document_state_changes():
    current_case = case(CaseStatus.M1_DOCUMENTS_PENDING)
    uploaded = _document_overview([document("UPLOADED")])
    review = _document_overview([document("ON_REVIEW")])

    first = _action_key(
        case=current_case,
        action=_priority_action(current_case, uploaded),
        documents=uploaded,
        consultation=None,
    )
    second = _action_key(
        case=current_case,
        action=_priority_action(current_case, review),
        documents=review,
        consultation=None,
    )

    assert first != second
    assert len(first) == 12
    assert len(second) == 12


def test_progress_bar_is_bounded_and_client_friendly():
    assert progress_bar(-5) == "○○○○○○○○○○ 0%"
    assert progress_bar(50) == "●●●●●○○○○○ 50%"
    assert progress_bar(120) == "●●●●●●●●●● 100%"


def test_my_case_uses_one_primary_action_and_document_aware_snapshot():
    source = read("app/bot/screens/my_case.py")

    assert "load_client_case_view" in source
    assert 'f"next_action:v2:{view.case_id}:{view.action_key}"' in source
    assert 'parts[1] == "v2"' in source
    assert "requested_action_key != view.action_key" in source
    assert "Данные дела или документов уже изменились" in source
    assert "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ" in source
    assert "ГОТОВНОСТЬ" in source
    assert "ЧТО МЕШАЕТ ПРОДОЛЖИТЬ" in source
    assert "message is not modified" in source
    assert 'c.data.startswith("next_action:")' in source


def test_home_status_and_my_case_share_the_same_presenter():
    common = read("app/bot/screens/common.py")
    my_case = read("app/bot/screens/my_case.py")

    assert "load_client_case_view" in common
    assert "load_client_case_view" in my_case
    assert "Документы: {view.documents.summary}" in common
    assert "Ваш следующий шаг" in common
    assert "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ" in my_case
    assert "Главный экран уже актуален" in common
    assert "PILOT_NEXT_ACTIONS" in common


def test_existing_case_status_actions_remain_available():
    assert CLIENT_ACTIONS["CALCULATED"].callback == "calc_decision_open"
    assert CLIENT_ACTIONS["CLIENT_DECISION"].callback == "consent_open"
    assert CLIENT_ACTIONS["M1_CONTRACT_READY"].callback == "contract_open"
    assert CLIENT_ACTIONS["M2_SLOT_PENDING"].callback == "consult_slot_open"
    assert CLIENT_ACTIONS["M2_CONSULTATION_BOOKED"].callback == (
        "consultation_booked_open"
    )
