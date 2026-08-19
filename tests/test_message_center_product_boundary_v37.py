from __future__ import annotations

import inspect

from app.api.message_center_product import (
    case_messages,
    mark_message_read,
    reply_to_client,
)


def test_case_messages_freezes_response_before_commit():
    source = inspect.getsource(case_messages)
    response_at = source.index("response = {")
    commit_at = source.index("await db.commit()")

    assert response_at < commit_at
    assert "_case_payload(" in source[response_at:commit_at]
    assert "[_message_payload(message) for message in messages]" in source[response_at:commit_at]


def test_staff_reply_freezes_orm_fields_before_durable_commit_then_delivers_outbox():
    source = inspect.getsource(reply_to_client)
    response_at = source.index("response = {")
    commit_at = source.index("await db.commit()", response_at)
    delivery_at = source.index("await _deliver_message_notifications", commit_at)

    assert response_at < commit_at < delivery_at
    frozen = source[response_at:commit_at]
    assert '"message_id": int(created.id)' in frozen
    assert '"case_id": int(case.id)' in frozen
    assert '"responsibility": responsibility.as_dict()' in frozen


def test_mark_read_freezes_response_before_commit_and_uses_guided_responsibility_scope():
    source = inspect.getsource(mark_message_read)
    response_at = source.index("response = {")
    commit_at = source.index("await db.commit()")

    assert "_responsibilities" in source
    assert "_allows_case" in source
    assert response_at < commit_at
    assert '"message_id": int(message.id)' in source[response_at:commit_at]
    assert '"case_id": int(case.id)' in source[response_at:commit_at]
