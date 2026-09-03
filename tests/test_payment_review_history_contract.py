from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api import payment_review_product
from app.api.payment_review_center import require_admin
from app.api.payment_review_history import (
    PAYMENT_REVIEW_HISTORY_LIMIT,
    PAYMENT_REVIEW_REQUIRED,
    PAYMENT_REVIEW_RESOLVED,
    get_payment_review_history,
    load_payment_review_history,
    serialize_payment_review_history_event,
)
from app.models.audit_log import AuditLog
from app.models.payment import Payment


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def _audit_event(
    *,
    event_id: int,
    payment_id: int,
    action: str,
    old_value: dict | None = None,
    new_value: dict | None = None,
    actor_type: str = "admin",
    actor_id: int | None = 17,
) -> AuditLog:
    return AuditLog(
        id=event_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type="case",
        entity_id=77,
        old_value=old_value or {},
        new_value={"payment_id": payment_id, **(new_value or {})},
        comment="private reconciliation note that must not leave AuditLog",
        chain_version=1,
        chain_sequence=9000 + event_id,
        previous_hash="a" * 64,
        event_hash="b" * 64,
        integrity_key_id="internal-key-id",
        created_at=NOW,
        updated_at=NOW,
    )


def _payment(payment_id: int = 101) -> Payment:
    return Payment(
        id=payment_id,
        case_id=77,
        payment_code="M2_CONSULTATION_PAYMENT",
        title="Payment Review contract fixture",
        amount=Decimal("5000.00"),
        currency="RUB",
        status="PAID",
        provider="test-provider",
        provider_payment_id="provider-secret-id",
        reservation_key="consultation:55:slot:66",
        created_at=NOW,
        updated_at=NOW,
    )


class _Scalars:
    def __init__(self, events: list[AuditLog]) -> None:
        self._events = events

    def all(self) -> list[AuditLog]:
        return list(self._events)


class _Result:
    def __init__(self, events: list[AuditLog]) -> None:
        self._events = events

    def scalars(self) -> _Scalars:
        return _Scalars(self._events)


class _FakeDb:
    def __init__(self, payment: Payment | None, events: list[AuditLog]) -> None:
        self.payment = payment
        self.events = events

    async def get(self, model, object_id):  # noqa: ANN001, ANN201
        if self.payment is None or int(object_id) != int(self.payment.id):
            return None
        return self.payment

    async def execute(self, _statement):  # noqa: ANN001, ANN201
        return _Result(self.events)


def test_required_history_projection_is_business_safe_not_raw_audit_export() -> None:
    event = _audit_event(
        event_id=1,
        payment_id=101,
        action=PAYMENT_REVIEW_REQUIRED,
        old_value={
            "status": "EXPIRED",
            "reservation_key": "must-not-leak",
            "provider_payload": {"secret": "old"},
        },
        new_value={
            "reason": "Позднее подтверждение провайдера",
            "provider_payload": {"secret": "new"},
            "internal_meta": {"ip": "127.0.0.1"},
        },
        actor_type="system",
        actor_id=None,
    )

    item = serialize_payment_review_history_event(event)

    assert item["kind"] == "required"
    assert item["origin_status"] == "EXPIRED"
    assert item["reason"] == "Позднее подтверждение провайдера"
    assert item["actor_type"] == "system"
    assert item["actor_id"] is None
    assert "comment" not in item
    assert "old_value" not in item
    assert "new_value" not in item
    assert "reservation_key" not in item
    assert "provider_payload" not in repr(item)
    assert "internal_meta" not in repr(item)
    assert "integrity_key_id" not in item
    assert "event_hash" not in item


def test_resolved_history_projection_keeps_decision_without_free_text_or_integrity_data() -> None:
    event = _audit_event(
        event_id=2,
        payment_id=101,
        action=PAYMENT_REVIEW_RESOLVED,
        old_value={"payment_status": "PAID_REVIEW"},
        new_value={
            "payment_status": "PAID",
            "decision": "confirm_existing",
            "consultation_id": 55,
            "slot_id": 66,
            "scheduled_at": "2026-09-02T09:00:00+00:00",
            "reservation_key": "consultation:55:slot:66",
        },
    )

    item = serialize_payment_review_history_event(event)

    assert item["kind"] == "resolved"
    assert item["origin_status"] == "PAID_REVIEW"
    assert item["resulting_status"] == "PAID"
    assert item["decision"] == "confirm_existing"
    assert item["consultation_id"] == 55
    assert item["slot_id"] == 66
    assert "comment" not in item
    assert "scheduled_at" not in item
    assert "reservation_key" not in item
    assert "chain_sequence" not in item
    assert "previous_hash" not in item


def test_history_filters_exact_payment_before_bounding_visible_timeline() -> None:
    target_events = [
        _audit_event(
            event_id=index + 1,
            payment_id=101,
            action=(
                PAYMENT_REVIEW_REQUIRED if index % 2 == 0 else PAYMENT_REVIEW_RESOLVED
            ),
            old_value={"payment_status": "PAID_REVIEW"},
            new_value={"decision": "confirm_existing", "payment_status": "PAID"},
        )
        for index in range(PAYMENT_REVIEW_HISTORY_LIMIT + 3)
    ]
    other_payment_events = [
        _audit_event(
            event_id=100 + index,
            payment_id=202,
            action=PAYMENT_REVIEW_REQUIRED,
            old_value={"status": "EXPIRED"},
            new_value={"reason": "other payment"},
        )
        for index in range(40)
    ]
    db = _FakeDb(_payment(), other_payment_events + target_events)

    result = asyncio.run(load_payment_review_history(db, payment_id=101))

    assert result["payment_id"] == 101
    assert result["case_id"] == 77
    assert result["payment_status"] == "PAID"
    assert result["event_count"] == PAYMENT_REVIEW_HISTORY_LIMIT
    assert result["total_event_count"] == PAYMENT_REVIEW_HISTORY_LIMIT + 3
    assert result["truncated"] is True
    assert all(event["event_id"] < 100 for event in result["events"])

    source = inspect.getsource(load_payment_review_history)
    assert ".limit(" not in source
    assert "_event_payment_id(event) == int(payment.id)" in source


def test_history_returns_404_for_unknown_payment() -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(load_payment_review_history(_FakeDb(None, []), payment_id=404))

    assert error.value.status_code == 404
    assert error.value.detail == "Платёж не найден"


def test_product_router_exposes_admin_only_read_only_history_route() -> None:
    route = next(
        (
            route
            for route in payment_review_product.router.routes
            if route.path == "/admin/payment-reviews/{payment_id}/history"
        ),
        None,
    )

    assert route is not None
    assert route.name == "get_payment_review_history"
    assert route.methods == {"GET"}
    assert route.endpoint is get_payment_review_history

    with pytest.raises(HTTPException) as error:
        require_admin(None)
    assert error.value.status_code == 403

    endpoint_source = inspect.getsource(get_payment_review_history)
    assert "require_admin(x_admin_token)" in endpoint_source
    assert "load_payment_review_history" in endpoint_source
