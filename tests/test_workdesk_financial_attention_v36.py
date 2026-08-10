from datetime import datetime, timedelta, timezone

from app.api.workdesk import (
    _append_payment_attention,
    _attention_item,
    _empty_payment_attention,
)
from app.domain.documents.document_workflow import describe_document_attention
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case


def make_case() -> Case:
    now = datetime.now(timezone.utc)
    return Case(
        id=77,
        case_number="WORKDESK-FIN-77",
        client_id=1,
        route="M2",
        status="M2_CONSULTATION_BOOKED",
        title="Финансовая задача",
        next_action="Подготовиться к консультации",
        assigned_lawyer_id=None,
        sla_status="ACTION_OVERDUE",
        sla_due_at=now - timedelta(hours=4),
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(minutes=10),
    )


def build_item(attention):
    return _attention_item(
        make_case(),
        unread_client_messages=2,
        latest_client_message_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        document_workflow=describe_document_attention(()),
        consultation_at=None,
        lawyer_name=None,
        payment_attention=attention,
    )


def test_payment_review_has_top_workdesk_priority():
    attention = _empty_payment_attention()
    _append_payment_attention(
        attention,
        payment_id=501,
        status=PaymentStatus.PAID_REVIEW,
        activity_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    _append_payment_attention(
        attention,
        payment_id=502,
        status=PaymentStatus.REFUND_PENDING,
        activity_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    item = build_item(attention)

    assert item is not None
    assert [reason["code"] for reason in item["reasons"]][:2] == [
        "payment_review",
        "refund",
    ]
    assert item["priority"] == 0
    assert item["financial_attention"] == {
        "payment_review_ids": [501],
        "refund_pending_ids": [502],
    }
    assert item["primary_action"]["label"] == "Сверить полученный платёж"
    assert item["primary_action"]["href"].endswith("payment_id=501")


def test_refund_is_primary_when_review_is_absent():
    attention = _empty_payment_attention()
    _append_payment_attention(
        attention,
        payment_id=601,
        status=PaymentStatus.REFUND_PENDING,
        activity_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )

    item = build_item(attention)

    assert item is not None
    assert item["reasons"][0]["code"] == "refund"
    assert item["priority"] == 1
    assert item["primary_action"]["label"] == "Обработать возврат"
    assert item["primary_action"]["href"].endswith("payment_id=601")
