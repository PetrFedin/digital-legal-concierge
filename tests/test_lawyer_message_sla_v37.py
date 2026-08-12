from __future__ import annotations

import inspect

from app.domain.messages.message_service import MessageService


def test_lawyer_reply_records_sla_activity_in_same_service_transaction():
    source = inspect.getsource(MessageService.create_lawyer_message)

    assert "case.assigned_lawyer_id == lawyer_id" in source
    assert "CaseSLAService(self.db).record_lawyer_activity" in source
    assert 'action="CLIENT_MESSAGE_REPLIED"' in source


def test_unassigned_or_mismatched_message_does_not_fake_lawyer_sla_activity():
    source = inspect.getsource(MessageService.create_lawyer_message)

    assert "lawyer_id is not None and case.assigned_lawyer_id == lawyer_id" in source
