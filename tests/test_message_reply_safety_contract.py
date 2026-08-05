from __future__ import annotations

import inspect
import re

from pydantic import ValidationError

from app.api.message_center import (
    MESSAGE_CENTER_HTML,
    ReplyPayload,
    case_messages,
    mark_message_read,
    message_center_status,
    reply_to_client,
    require_staff_scope,
)
from app.domain.messages.message_service import MessageService


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _function(name: str) -> str:
    marker = f"async function {name}("
    start = MESSAGE_CENTER_HTML.index(marker)
    end = MESSAGE_CENTER_HTML.find("\nasync function ", start + 1)
    if end < 0:
        end = MESSAGE_CENTER_HTML.index("\nboot();", start)
    return MESSAGE_CENTER_HTML[start:end]


def test_reply_payload_requires_the_last_seen_message_snapshot():
    try:
        ReplyPayload(text="Ответ")
    except ValidationError as error:
        assert "expected_last_message_id" in str(error)
    else:
        raise AssertionError("reply snapshot must be required")

    empty_thread = ReplyPayload(text="Ответ", expected_last_message_id=None)
    existing_thread = ReplyPayload(text="Ответ", expected_last_message_id=17)
    assert empty_thread.expected_last_message_id is None
    assert existing_thread.expected_last_message_id == 17


def test_lawyer_scope_is_resolved_to_a_concrete_lawyer_card():
    source = inspect.getsource(require_staff_scope)
    compact = _compact(source)

    assert "BROAD_ACCESS_ROLES" in source
    assert "roles.intersection(BROAD_ACCESS_ROLES)" in source
    assert "require_lawyer_actor(db,token)" in compact
    assert "lawyer_id=lawyer_actor.lawyer.id" in compact


def test_message_listing_and_thread_access_are_scoped_for_lawyers():
    status_source = inspect.getsource(message_center_status)
    thread_source = inspect.getsource(case_messages)
    read_source = inspect.getsource(mark_message_read)

    assert "Case.assigned_lawyer_id == scope.lawyer_id" in status_source
    assert "scope.allows_case(case)" in status_source
    assert "_ensure_case_access(scope, case)" in thread_source
    assert "_ensure_case_access(scope, case)" in read_source
    assert "except Exception" in thread_source
    assert "await db.rollback()" in thread_source
    assert "except Exception" in read_source
    assert "await db.rollback()" in read_source


def test_message_writes_lock_the_case_before_creating_records():
    lock_source = inspect.getsource(MessageService.lock_case)
    client_wrapper = inspect.getsource(MessageService.create_client_message)
    client_create = inspect.getsource(MessageService.get_or_create_client_message)
    lawyer_source = inspect.getsource(MessageService.create_lawyer_message)

    assert ".with_for_update()" in lock_source
    assert "case = await self.lock_case(case.id)" in client_create
    assert "await self.get_or_create_client_message(" in client_wrapper
    assert "source_message_id=source_message_id" in client_wrapper
    assert "async with self.db.begin_nested()" in client_create
    assert "await self.db.flush()" in client_create
    assert "case = await self.lock_case(case.id)" in lawyer_source
    assert "await self.db.flush()" in lawyer_source


def test_reply_checks_snapshot_and_commits_outbox_before_delivery():
    source = inspect.getsource(reply_to_client)

    case_lock = source.index("case = await service.lock_case(case_id)")
    access_check = source.index("_ensure_case_access(scope, case)")
    latest = source.index("latest_message_id = await service.latest_message_id(case_id)")
    stale_check = source.index("latest_message_id != payload.expected_last_message_id")
    create = source.index("created = await service.create_lawyer_message")
    outbox = source.index("notifications = await NotificationEngine(db).emit")
    durable_commit = source.index("await db.commit()", outbox)
    delivery = source.index(
        "delivery = await _deliver_message_notifications(db, notification_ids)",
        durable_commit,
    )

    assert (
        case_lock
        < access_check
        < latest
        < stale_check
        < create
        < outbox
        < durable_commit
        < delivery
    )
    assert "В диалоге появились новые сообщения" in source
    assert "Нельзя отправить ответ от имени другого юриста" in source
    assert "except HTTPException" in source
    assert "except LookupError" in source
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 3
    assert 'event_code="STAFF_MESSAGE_REPLIED"' in source
    assert 'dedupe_key=f"case:{case.id}:message:{created.id}:staff-reply"' in source
    assert '"latest_message_id": message_id' in source
    assert '"delivery": delivery' in source
    assert "bot.send_message" not in source


def test_reply_ui_is_single_flight_and_preserves_draft_on_failures():
    compact = _compact(MESSAGE_CENTER_HTML)
    function = _function("sendReply")

    assert "sendPending=false" in compact
    assert "if(sendPending||!currentCaseId)return" in compact
    assert "sendPending=true" in compact
    assert "sendPending=false" in compact
    assert "button.disabled=true" in compact
    assert "button.disabled=false" in compact
    assert "replyText.disabled=true" in compact
    assert "replyText.disabled=false" in compact
    assert "finally" in function
    assert "sendReply(this)" in compact
    assert "expected_last_message_id:lastMessageId" in compact
    assert function.index("await api(") < function.index(
        "replyText.value=''", function.index("await api(")
    )
    assert "Ответ не отправлен и не сохранён" in function
    assert "Текст ответа сохранён в поле" in function


def test_message_loads_abort_stale_requests_and_safe_interval_handles_failures():
    compact = _compact(MESSAGE_CENTER_HTML)
    inbox = _function("loadMessages")
    dialog = _function("openCase")

    assert "inboxController.abort()" in compact
    assert "dialogController.abort()" in compact
    assert "newAbortController()" in compact
    assert "signal:controller.signal" in compact
    assert "e.name==='AbortError'" in compact
    assert "finally" in inbox
    assert "finally" in dialog
    assert "setInterval(()=>{voidloadMessages(null,false)},60000)" in compact


def test_message_feedback_is_accessible_and_transport_is_not_cached():
    compact = _compact(MESSAGE_CENTER_HTML)

    assert 'role="status"' in MESSAGE_CENTER_HTML
    assert 'aria-live="polite"' in MESSAGE_CENTER_HTML
    assert "credentials:'same-origin'" in MESSAGE_CENTER_HTML
    assert "cache:'no-store'" in MESSAGE_CENTER_HTML
    assert "if(!r.ok)" in compact
    assert "Ответ отправлен и сохранён. Клиент получил его в Telegram." in MESSAGE_CENTER_HTML
    assert "Ответ сохранён и поставлен в очередь повторной Telegram-доставки." in MESSAGE_CENTER_HTML
    assert "Но экран не обновился" in MESSAGE_CENTER_HTML
    assert "alert(" not in MESSAGE_CENTER_HTML
