from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.payment_webhooks import fake_payload_summary, yookassa_payload_summary
from app.config import settings
from app.models import Base
from app.models.payment_webhook_event import PaymentWebhookEvent
from app.security.payment_webhook_ledger import (
    canonical_payload_sha256,
    claim_webhook_event,
    derive_event_key,
    finish_webhook_event,
    read_limited_body,
)


class FakeRequest:
    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None):
        self._chunks = chunks
        self.headers = headers or {}

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


async def database_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'webhooks.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


def configure(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "max_payment_webhook_kb", 1)
    monkeypatch.setattr(settings, "payment_webhook_processing_timeout_seconds", 30)
    monkeypatch.setattr(settings, "payment_webhook_max_attempts", 3)


def test_canonical_hash_and_event_key_are_deterministic():
    left = {"event": "payment.succeeded", "object": {"id": "pay-1", "paid": True}}
    right = {"object": {"paid": True, "id": "pay-1"}, "event": "payment.succeeded"}
    assert canonical_payload_sha256(left) == canonical_payload_sha256(right)
    assert derive_event_key("yookassa", "payment.succeeded", "pay-1") == derive_event_key(
        "yookassa", "payment.succeeded", "pay-1"
    )
    assert derive_event_key("yookassa", "payment.succeeded", "pay-1") != derive_event_key(
        "yookassa", "payment.canceled", "pay-1"
    )


def test_payload_summaries_do_not_persist_sensitive_fields():
    fake = fake_payload_summary(
        {
            "event_id": "evt-1",
            "payment_id": 15,
            "status": "paid",
            "email": "client@example.test",
            "card_number": "4111111111111111",
            "secret": "do-not-store",
        }
    )
    assert fake == {"event_id": "evt-1", "payment_id": "15", "status": "paid"}

    yookassa = yookassa_payload_summary(
        {
            "event": "payment.succeeded",
            "object": {
                "id": "provider-1",
                "status": "succeeded",
                "amount": {"value": "100.00", "currency": "RUB"},
                "payment_method": {
                    "card": {"first6": "411111", "last4": "1111"}
                },
                "metadata": {"client_email": "client@example.test"},
            },
        }
    )
    encoded = str(yookassa)
    assert "411111" not in encoded
    assert "client@example.test" not in encoded
    assert yookassa["provider_payment_id"] == "provider-1"


@pytest.mark.asyncio
async def test_processed_event_is_idempotent_and_attempt_is_counted(tmp_path, monkeypatch):
    configure(monkeypatch)
    engine, factory = await database_factory(tmp_path)
    payload = {"event": "payment.succeeded", "object": {"id": "pay-1"}}
    digest = canonical_payload_sha256(payload)
    event_key = derive_event_key("yookassa", "payment.succeeded", "pay-1", "succeeded")

    async with factory() as db:
        first = await claim_webhook_event(
            db,
            provider="yookassa",
            event_key=event_key,
            event_type="payment.succeeded",
            provider_payment_id="pay-1",
            payload_sha256=digest,
            payload_summary={"provider_payment_id": "pay-1"},
        )
        assert first.should_process is True
        assert first.duplicate is False
        await finish_webhook_event(db, first.event.id, status="PROCESSED", response_code=200)
        await db.commit()

        second = await claim_webhook_event(
            db,
            provider="yookassa",
            event_key=event_key,
            event_type="payment.succeeded",
            provider_payment_id="pay-1",
            payload_sha256=digest,
            payload_summary={"provider_payment_id": "pay-1"},
        )
        assert second.should_process is False
        assert second.duplicate is True
        assert second.payload_conflict is False
        assert second.event.status == "PROCESSED"
        assert second.event.attempt_count == 2
        await db.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_same_event_key_with_changed_payload_is_dead_lettered(tmp_path, monkeypatch):
    configure(monkeypatch)
    engine, factory = await database_factory(tmp_path)
    event_key = derive_event_key("fake", "evt-42", "7", "paid")
    original = canonical_payload_sha256(
        {"event_id": "evt-42", "payment_id": 7, "status": "paid", "amount": "10"}
    )
    altered = canonical_payload_sha256(
        {"event_id": "evt-42", "payment_id": 7, "status": "paid", "amount": "999"}
    )

    async with factory() as db:
        first = await claim_webhook_event(
            db,
            provider="fake",
            event_key=event_key,
            event_type="payment.paid",
            provider_payment_id="7",
            payload_sha256=original,
            payload_summary={"event_id": "evt-42"},
        )
        await finish_webhook_event(db, first.event.id, status="PROCESSED", response_code=200)
        await db.commit()

        conflict = await claim_webhook_event(
            db,
            provider="fake",
            event_key=event_key,
            event_type="payment.paid",
            provider_payment_id="7",
            payload_sha256=altered,
            payload_summary={"event_id": "evt-42"},
        )
        assert conflict.should_process is False
        assert conflict.payload_conflict is True
        assert conflict.event.status == "DEAD_LETTER"
        assert conflict.event.response_code == 409
        assert conflict.event.error_code == "event_payload_mismatch"
        assert conflict.event.payload_sha256 == original
        await db.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_processing_event_can_be_reclaimed(tmp_path, monkeypatch):
    configure(monkeypatch)
    engine, factory = await database_factory(tmp_path)
    payload = {"payment_id": 5, "status": "paid"}
    digest = canonical_payload_sha256(payload)
    event_key = derive_event_key("fake", digest, 5, "paid")

    async with factory() as db:
        first = await claim_webhook_event(
            db,
            provider="fake",
            event_key=event_key,
            event_type="payment.paid",
            provider_payment_id="5",
            payload_sha256=digest,
            payload_summary={"payment_id": "5"},
        )
        first.event.processing_started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.commit()

        reclaimed = await claim_webhook_event(
            db,
            provider="fake",
            event_key=event_key,
            event_type="payment.paid",
            provider_payment_id="5",
            payload_sha256=digest,
            payload_summary={"payment_id": "5"},
        )
        assert reclaimed.should_process is True
        assert reclaimed.duplicate is True
        assert reclaimed.event.status == "PROCESSING"
        assert reclaimed.event.attempt_count == 2
        assert reclaimed.event.error_code is None
        await db.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_inflight_event_reaches_dead_letter_attempt_limit(
    tmp_path,
    monkeypatch,
):
    configure(monkeypatch)
    monkeypatch.setattr(settings, "payment_webhook_max_attempts", 2)
    engine, factory = await database_factory(tmp_path)
    payload = {"payment_id": 8, "status": "paid"}
    digest = canonical_payload_sha256(payload)
    event_key = derive_event_key("fake", digest, 8, "paid")

    async with factory() as db:
        first = await claim_webhook_event(
            db,
            provider="fake",
            event_key=event_key,
            event_type="payment.paid",
            provider_payment_id="8",
            payload_sha256=digest,
            payload_summary={"payment_id": "8"},
        )
        await db.commit()
        second = await claim_webhook_event(
            db,
            provider="fake",
            event_key=event_key,
            event_type="payment.paid",
            provider_payment_id="8",
            payload_sha256=digest,
            payload_summary={"payment_id": "8"},
        )
        assert second.should_process is False
        await db.commit()
        third = await claim_webhook_event(
            db,
            provider="fake",
            event_key=event_key,
            event_type="payment.paid",
            provider_payment_id="8",
            payload_sha256=digest,
            payload_summary={"payment_id": "8"},
        )
        assert third.should_process is False
        assert third.event.status == "DEAD_LETTER"
        assert third.event.error_code == "max_attempts_exceeded"
        assert third.event.attempt_count == 3
        await db.commit()
        persisted = await db.get(PaymentWebhookEvent, first.event.id)
        assert persisted.status == "DEAD_LETTER"

    await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_body_is_bounded_before_json_parsing(monkeypatch):
    configure(monkeypatch)
    request = FakeRequest([], {"content-length": "2048"})
    with pytest.raises(HTTPException) as too_large_header:
        await read_limited_body(request)
    assert too_large_header.value.status_code == 413

    request = FakeRequest([b"a" * 700, b"b" * 400])
    with pytest.raises(HTTPException) as too_large_stream:
        await read_limited_body(request)
    assert too_large_stream.value.status_code == 413

    request = FakeRequest([b'{"ok":', b"true}"])
    assert await read_limited_body(request) == b'{"ok":true}'
