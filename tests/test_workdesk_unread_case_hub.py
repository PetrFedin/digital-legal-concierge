from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.api.workdesk import _attention_item, _attention_sort_key
from app.bot.client_case_view import ClientAction, _action_key, _document_overview
from app.bot.keyboards import main_menu
from app.bot.screens.common import _primary_action
from app.bot.screens.my_case import _case_buttons
from app.domain.statuses.case_statuses import CaseStatus


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _case(
    *,
    case_id: int,
    status: str = CaseStatus.M1_LAWYER_REVIEW,
    lawyer_id: int | None = None,
    sla_status: str = "ACTION_PENDING",
    due_hour: int = 15,
):
    return SimpleNamespace(
        id=case_id,
        case_number=f"DLC-2026-{case_id:06d}",
        route="M1",
        status=status,
        assigned_lawyer_id=lawyer_id,
        sla_status=sla_status,
        next_action="Проверить документы",
        sla_due_at=datetime(2026, 8, 5, due_hour, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 1, case_id, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 5, 10, tzinfo=timezone.utc),
    )


def test_attention_center_deduplicates_reasons_and_keeps_safe_first_action():
    item = _attention_item(
        _case(
            case_id=1,
            lawyer_id=None,
            sla_status="ACTION_OVERDUE",
        ),
        has_documents=True,
        consultation_at=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
        lawyer_name=None,
    )

    assert item is not None
    assert [reason["code"] for reason in item["reasons"]] == [
        "overdue",
        "unassigned",
        "documents",
        "consultation",
    ]
    assert item["priority"] == 0
    assert item["primary_action"] == {
        "kind": "auto_assign",
        "label": "Назначить юриста",
        "endpoint": "/admin/cases/1/auto-assign",
        "payload": {
            "expected_lawyer_id": None,
            "expected_status": CaseStatus.M1_LAWYER_REVIEW,
        },
    }


def test_attention_sort_places_overdue_before_other_work():
    overdue = _attention_item(
        _case(
            case_id=1,
            lawyer_id=7,
            sla_status="FIRST_RESPONSE_OVERDUE",
        ),
        has_documents=False,
        consultation_at=None,
        lawyer_name="Юрист",
    )
    unassigned = _attention_item(
        _case(case_id=2, lawyer_id=None),
        has_documents=False,
        consultation_at=None,
        lawyer_name=None,
    )
    documents = _attention_item(
        _case(case_id=3, lawyer_id=7),
        has_documents=True,
        consultation_at=None,
        lawyer_name="Юрист",
    )

    items = [documents, unassigned, overdue]
    assert all(item is not None for item in items)
    ordered = sorted(items, key=_attention_sort_key)
    assert [item["id"] for item in ordered] == [1, 2, 3]


def test_workdesk_routes_actions_to_domain_workspaces_without_generic_status_write():
    source = read("app/api/workdesk.py")
    ui = read("app/api/workdesk_ui.py")

    assert '@router.get("/admin/workdesk/attention")' in source
    assert "'/admin/cases/'+id+'/auto-assign'" in ui
    assert "/document-access/review/ui" in ui
    assert "/admin/consultation-outcomes/ui" in ui
    assert "/admin/sla/ui" in ui
    assert "/message-center/ui?case_id=${x.id}" in ui
    assert "Очередь пуста" in ui
    assert "Повторить" in ui
    assert "CaseService(db).change_status" not in source
    assert "/advance" not in source
    assert "force=True" not in source
    assert "тестовый платёж" not in (source + ui).lower()


def test_operator_makes_guided_workdesk_primary_and_mounts_router():
    source = read("app/api/operator.py")

    assert 'href="/admin/workdesk/ui"' in source
    assert "Единый рабочий стол" in source
    assert '"admin": "/admin/workdesk/ui"' in source
    assert '"admin_legacy": "/admin-ui"' in source
    assert "router.include_router(workdesk_router)" in source


def _view(*, unread: int, action: ClientAction | None):
    return SimpleNamespace(
        unread_team_messages=unread,
        action=action,
        case_id=7,
        action_key="snapshot",
    )


def test_unread_team_reply_becomes_home_primary_action():
    action = ClientAction("Загрузить документы", "documents_open", "Добавьте файл")
    assert _primary_action(_view(unread=2, action=action)) == (
        "💬 Прочитать ответ команды (2)",
        "message_history",
    )


def test_my_case_places_unread_reply_before_legal_action():
    action = ClientAction("Загрузить документы", "documents_open", "Добавьте файл")
    buttons = _case_buttons(_view(unread=3, action=action))

    assert buttons[0] == ("💬 Прочитать новые ответы (3)", "message_history")
    assert buttons[1] == (
        "▶️ Загрузить документы",
        "next_action:v2:7:snapshot",
    )


def test_unread_reply_changes_case_snapshot():
    case = SimpleNamespace(
        id=7,
        status=CaseStatus.M1_DOCUMENTS_PENDING,
        updated_at=datetime(2026, 8, 5, 10, tzinfo=timezone.utc),
    )
    documents = _document_overview([])
    action = ClientAction("Загрузить документы", "documents_open", "Добавьте файл")

    before = _action_key(
        case=case,
        action=action,
        documents=documents,
        consultation=None,
        unread_team_messages=0,
        latest_team_message_at=None,
    )
    after = _action_key(
        case=case,
        action=action,
        documents=documents,
        consultation=None,
        unread_team_messages=1,
        latest_team_message_at=datetime(2026, 8, 5, 11, tzinfo=timezone.utc),
    )

    assert before != after


def test_unread_reply_is_counted_and_direct_dialog_actions_are_available():
    presenter = read("app/bot/client_case_view.py")
    messages = read("app/domain/messages/message_service.py")
    common = read("app/bot/screens/common.py")
    my_case = read("app/bot/screens/my_case.py")

    assert "unread_lawyer_summary" in messages
    assert "func.count(Message.id)" in messages
    assert 'Message.sender_type == "lawyer"' in messages
    assert "unread_team_messages: int = 0" in presenter
    assert "latest_team_message_at" in presenter
    assert "str(unread_team_messages)" in presenter
    assert "Новые ответы команды" in common
    assert "Новые ответы команды" in my_case
    assert "либо в переписке появился новый ответ" in my_case

    callbacks = [
        button.callback_data
        for row in main_menu(case_exists=True, payments_enabled=False).inline_keyboard
        for button in row
    ]
    assert "message_history" in callbacks
    assert "message_create" in callbacks
    assert "calc_start" not in callbacks