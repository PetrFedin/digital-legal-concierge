from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.bot.client_case_view import (
    CLIENT_STAGE_COPY,
    ClientAction,
    DocumentOverview,
    _action_key,
    client_stage_projection,
    next_action_text,
)
from app.domain.cases.case_activity import present_case_activity
from app.domain.statuses.case_statuses import CaseStatus
from app.models.audit_log import AuditLog


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def docs(
    *,
    uploaded: int = 0,
    review: int = 0,
    replacement: int = 0,
    legacy_attention: int = 0,
) -> DocumentOverview:
    return DocumentOverview(
        current_count=uploaded + review + replacement + legacy_attention,
        archived_count=0,
        uploaded_count=uploaded,
        review_count=review,
        approved_count=0,
        replacement_count=replacement,
        summary="Тестовая клиентская сводка",
        blocker=(
            "Требуется новая версия одного или нескольких документов."
            if replacement
            else None
        ),
        latest_updated_at=None,
        legacy_attention_count=legacy_attention,
    )


def case(status: str, *, route: str | None = None, version: int = 1):
    return SimpleNamespace(
        id=17,
        status=status,
        route=route,
        version=version,
        updated_at=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
        next_action="INTERNAL CRM NEXT ACTION MUST NEVER LEAK",
    )


def test_every_supported_case_status_has_explicit_client_stage_copy() -> None:
    expected = {str(status) for status in CaseStatus}
    assert set(CLIENT_STAGE_COPY) == expected


def test_unknown_or_conflicting_state_fails_closed_without_raw_internal_copy() -> None:
    unknown = client_stage_projection(
        case("INTERNAL_APPROVAL_X", route="M1"),
        docs(),
    )
    assert unknown.status_label == "Статус уточняется"
    assert unknown.action is not None
    assert unknown.action.callback == "contact_lawyer"
    assert "INTERNAL_APPROVAL_X" not in " ".join(
        (
            unknown.status_label,
            unknown.now_text,
            unknown.client_requirement,
            unknown.blocker or "",
            unknown.action.description,
        )
    )

    conflict = client_stage_projection(
        case(CaseStatus.M1_COURT_STAGE, route="M2"),
        docs(),
    )
    assert conflict.status_label == "Статус уточняется"
    assert conflict.action is not None
    assert conflict.action.callback == "contact_lawyer"


def test_document_facts_can_only_own_primary_action_inside_m1_document_contour() -> None:
    stale_replacement = docs(replacement=1)

    court = client_stage_projection(
        case(CaseStatus.M1_COURT_STAGE, route="M1"),
        stale_replacement,
    )
    assert court.action is not None
    assert court.action.callback == "court_status"
    assert court.blocker is None

    requested = client_stage_projection(
        case(CaseStatus.M1_DOCS_REQUESTED, route="M1"),
        stale_replacement,
    )
    assert requested.action is not None
    assert requested.action.callback == "documents_open"
    assert requested.blocker is not None

    consultation = client_stage_projection(
        case(CaseStatus.M2_DOCUMENTS_OPTIONAL, route="M2"),
        stale_replacement,
    )
    assert consultation.action is not None
    assert consultation.action.callback == "consult_booking_start"
    assert consultation.blocker is None


def test_persisted_case_next_action_is_not_a_client_copy_fallback() -> None:
    unknown_case = case("UNSUPPORTED_INTERNAL_STATE", route="M1")
    assert "INTERNAL CRM" not in next_action_text(unknown_case)

    source = read("app/bot/client_case_view.py")
    assert "case.next_action" not in source


def test_my_case_document_blocker_never_embeds_lawyer_free_text() -> None:
    source = read("app/bot/client_case_view.py")
    blocker_section = source.split("def _document_overview", 1)[1].split(
        "def _priority_action", 1
    )[0]
    assert "lawyer_comment" not in blocker_section
    assert "Требуется новая версия одного или нескольких документов" in blocker_section


def test_client_history_does_not_export_document_review_audit_comment() -> None:
    log = AuditLog(
        actor_type="lawyer",
        actor_id=9,
        action="DOCUMENT_REVIEW_DECISION",
        entity_type="case",
        entity_id=17,
        new_value={"title": "ДДУ", "status": "NEEDS_REUPLOAD"},
        comment="INTERNAL LEGAL REASONING: do not expose",
    )
    log.id = 101
    log.created_at = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

    client_item = present_case_activity(log, audience="client")
    staff_item = present_case_activity(log, audience="staff")

    assert client_item is not None
    assert client_item.detail is not None
    assert "INTERNAL LEGAL REASONING" not in client_item.detail
    assert staff_item is not None
    assert staff_item.detail is not None
    assert "INTERNAL LEGAL REASONING" in staff_item.detail


def test_projection_snapshot_changes_when_payment_or_case_version_changes() -> None:
    action = ClientAction("Открыть этап", "court_status", "Откройте актуальный этап.")
    document_overview = docs()
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    payment = SimpleNamespace(id=3, status="PENDING", updated_at=now)

    first = _action_key(
        case=case(CaseStatus.M1_COURT_STAGE, route="M1", version=7),
        action=action,
        documents=document_overview,
        consultation=None,
        payments=[payment],
        history_event_id=44,
    )
    payment.status = "PAID"
    second = _action_key(
        case=case(CaseStatus.M1_COURT_STAGE, route="M1", version=7),
        action=action,
        documents=document_overview,
        consultation=None,
        payments=[payment],
        history_event_id=44,
    )
    third = _action_key(
        case=case(CaseStatus.M1_COURT_STAGE, route="M1", version=8),
        action=action,
        documents=document_overview,
        consultation=None,
        payments=[payment],
        history_event_id=44,
    )

    assert first != second
    assert second != third


def test_home_and_my_case_render_the_same_projection_contract() -> None:
    my_case = read("app/bot/screens/my_case.py")
    home = read("app/bot/screens/common.py")

    for token in (
        "view.status_label",
        "view.now_text",
        "view.client_requirement",
        "view.blocker",
        "view.next_action",
        "view.documents.summary",
        "view.payments_summary",
        "view.history_summary",
    ):
        assert token in my_case
        assert token in home

    assert "ТРЕБУЕТСЯ ОТ ВАС" in my_case
    assert "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ" in my_case
    assert "СВОДКА" in my_case
    assert "view.documents.blocker" not in my_case
    assert "view.documents.blocker" not in home


def test_unread_message_is_visible_but_does_not_silently_replace_projected_action() -> None:
    common = read("app/bot/screens/common.py")
    primary = common.split("def _primary_action", 1)[1].split(
        "def _selection_required_text", 1
    )[0]

    assert primary.index("if view.action:") < primary.index(
        "if view.unread_team_messages:"
    )
