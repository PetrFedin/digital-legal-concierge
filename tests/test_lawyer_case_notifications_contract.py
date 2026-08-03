from __future__ import annotations

import inspect

import pytest

from app.api.lawyer import accept, request_docs, transfer_to_m2
from app.domain.notifications.notification_rules import NOTIFICATION_RULES
from app.domain.notifications.notification_templates import TEMPLATES


@pytest.mark.parametrize(
    (
        "endpoint",
        "event_code",
        "transition_marker",
        "dedupe_marker",
    ),
    (
        (
            accept,
            "M1_CASE_ACCEPTED",
            "accept_m1_case",
            "lawyer-accept",
        ),
        (
            request_docs,
            "M1_DOCUMENTS_REQUESTED",
            "request_more_documents",
            "docs-request",
        ),
        (
            transfer_to_m2,
            "M1_CASE_TRANSFERRED_TO_M2",
            "transfer_m1_to_m2",
            "route-m2",
        ),
    ),
)
def test_lawyer_case_decision_emits_notification_in_same_transaction(
    endpoint,
    event_code: str,
    transition_marker: str,
    dedupe_marker: str,
):
    source = inspect.getsource(endpoint)

    assert "source_version = case.updated_at.isoformat()" in source
    assert transition_marker in source
    assert "await NotificationEngine(db).emit(" in source
    assert f'event_code="{event_code}"' in source
    assert dedupe_marker in source
    assert "source_version" in source[source.index("dedupe_key=") :]
    assert source.index(transition_marker) < source.index("NotificationEngine(db).emit")
    assert source.index("NotificationEngine(db).emit") < source.index("await db.commit()")
    assert source.count("await db.rollback()") >= 3


def test_lawyer_decision_notification_rules_target_expected_recipients():
    assert NOTIFICATION_RULES["M1_CASE_ACCEPTED"] == {
        "recipients": ["client"],
        "template": "m1_case_accepted",
    }
    assert NOTIFICATION_RULES["M1_DOCUMENTS_REQUESTED"] == {
        "recipients": ["client"],
        "template": "m1_documents_requested",
    }
    assert NOTIFICATION_RULES["M1_CASE_TRANSFERRED_TO_M2"] == {
        "recipients": ["client", "admin"],
        "template": "m1_case_transferred_to_m2",
    }


@pytest.mark.parametrize(
    ("template_name", "payload", "required_text"),
    (
        (
            "m1_case_accepted",
            {"case_number": "DLC-2026-000001", "next_action": "Подписать договор"},
            "принято юристом",
        ),
        (
            "m1_documents_requested",
            {
                "case_number": "DLC-2026-000001",
                "request": "Загрузите договор и чеки",
                "next_action": "Загрузить документы",
            },
            "Загрузите договор и чеки",
        ),
        (
            "m1_case_transferred_to_m2",
            {"case_number": "DLC-2026-000001", "next_action": "Описать ситуацию"},
            "консультационный маршрут",
        ),
    ),
)
def test_lawyer_decision_templates_format_without_fallback(
    template_name: str,
    payload: dict,
    required_text: str,
):
    rendered = TEMPLATES[template_name].format(**payload)

    assert "DLC-2026-000001" in rendered
    assert required_text in rendered
    assert "{" not in rendered
    assert "}" not in rendered
