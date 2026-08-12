from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.workdesk_timeline import (
    _category,
    _serialize_event,
    _visible_event,
)


def _event(**overrides):
    values = {
        "id": 17,
        "created_at": datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc),
        "actor_type": "lawyer",
        "action": "CASE_SLA_LAWYER_ACTIVITY",
        "comment": "Клиенту дан ответ по сроку.",
        "old_value": {"sla_status": "ACTION_OVERDUE", "secret": "never expose"},
        "new_value": {"sla_status": "ACTION_PENDING", "token": "never expose"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_timeline_serializes_human_context_without_raw_audit_payloads():
    item = _serialize_event(_event())

    assert item["id"] == 17
    assert item["actor_label"] == "Юрист"
    assert item["category"] == "sla"
    assert item["title"] == "Зафиксировано действие юриста"
    assert item["detail"] == "Клиенту дан ответ по сроку."
    assert "old_value" not in item
    assert "new_value" not in item
    assert "secret" not in str(item)
    assert "token" not in str(item)


def test_timeline_labels_case_history_admin_actor_as_administrator():
    item = _serialize_event(_event(actor_type="admin_user", action="M1_CLAIM_SENT"))
    assert item["actor_label"] == "Администратор"


def test_timeline_categories_match_workdesk_visual_groups():
    assert _category("PAYMENT_PAID") == "payments"
    assert _category("CLIENT_MESSAGE_CREATED") == "messages"
    assert _category("DOCUMENT_STATUS_CHANGED") == "documents"
    assert _category("M2_CONSULTATION_DONE") == "consultation"
    assert _category("CASE_SLA_ACTION_OVERDUE") == "sla"


def test_timeline_filters_session_and_token_noise():
    assert not _visible_event(_event(action="ADMIN_SESSION_VIEWED"))
    assert not _visible_event(_event(action="ACCESS_TOKEN_ROTATED"))
    assert _visible_event(_event(action="M1_CLAIM_SENT"))


def test_timeline_comment_is_bounded_for_readable_case_card():
    item = _serialize_event(_event(comment="x" * 1000))
    assert len(item["detail"]) == 700
