from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.payment_review_center import payment_review_conflict_snapshot
from app.models.case import Case
from app.models.payment import Payment


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


class _Scalars:
    def __init__(self, events: list[SimpleNamespace]) -> None:
        self._events = events

    def all(self) -> list[SimpleNamespace]:
        return list(self._events)


class _Result:
    def __init__(self, events: list[SimpleNamespace]) -> None:
        self._events = events

    def scalars(self) -> _Scalars:
        return _Scalars(self._events)


class _FakeDb:
    def __init__(self) -> None:
        self.payment = SimpleNamespace(
            id=101,
            case_id=77,
            status="PAID",
            updated_at=NOW,
        )
        self.case = SimpleNamespace(
            id=77,
            status="M2_CONSULTATION_BOOKED",
            next_action=None,
        )
        self.events = [
            SimpleNamespace(
                new_value={
                    "payment_id": 101,
                    "decision": "confirm_existing",
                    "consultation_id": 55,
                    "slot_id": 66,
                    "orphan_consultation_id": None,
                    "orphan_slot_id": None,
                    "reservation_key": "private-reservation-key",
                    "provider_payload": {"secret": "must-not-leak"},
                },
                actor_id=17,
                comment="private reconciliation note",
                created_at=NOW,
            )
        ]

    async def get(self, model, object_id):  # noqa: ANN001, ANN201
        if model is Payment and int(object_id) == 101:
            return self.payment
        if model is Case and int(object_id) == 77:
            return self.case
        return None

    async def execute(self, _statement):  # noqa: ANN001, ANN201
        return _Result(self.events)


def test_stale_409_snapshot_exposes_server_truth_without_free_text_audit_comment() -> None:
    snapshot = asyncio.run(
        payment_review_conflict_snapshot(_FakeDb(), payment_id=101)
    )

    assert snapshot["snapshot_available"] is True
    assert snapshot["payment_id"] == 101
    assert snapshot["payment_status"] == "PAID"
    assert snapshot["case_id"] == 77
    assert snapshot["case_status"] == "M2_CONSULTATION_BOOKED"
    assert snapshot["resolution"] == {
        "decision": "confirm_existing",
        "consultation_id": 55,
        "slot_id": 66,
        "orphan_consultation_id": None,
        "orphan_slot_id": None,
        "actor_id": 17,
        "resolved_at": NOW.isoformat(),
    }

    serialized = repr(snapshot)
    assert "comment" not in snapshot["resolution"]
    assert "private reconciliation note" not in serialized
    assert "private-reservation-key" not in serialized
    assert "must-not-leak" not in serialized
