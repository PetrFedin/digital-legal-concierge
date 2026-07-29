from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.payment_webhook_event import PaymentWebhookEvent

FINAL_STATUSES = {"PROCESSED", "IGNORED", "DEAD_LETTER"}


@dataclass(frozen=True)
class WebhookClaim:
    event: PaymentWebhookEvent
    should_process: bool
    duplicate: bool


def canonical_payload_sha256(payload: dict) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def derive_event_key(provider: str, *parts: object) -> str:
    value = "|".join([provider, *(str(part or "") for part in parts)])
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def read_limited_body(request: Request) -> bytes:
    max_bytes = max(1, int(settings.max_payment_webhook_kb)) * 1024
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(413, "payment webhook payload too large")
        except ValueError:
            raise HTTPException(400, "invalid content-length")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, "payment webhook payload too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def claim_webhook_event(
    db: AsyncSession,
    *,
    provider: str,
    event_key: str,
    event_type: str,
    provider_payment_id: str | None,
    payload_sha256: str,
    payload_summary: dict,
) -> WebhookClaim:
    now = datetime.now(timezone.utc)
    event = PaymentWebhookEvent(
        provider=provider,
        event_key=event_key,
        event_type=event_type,
        provider_payment_id=provider_payment_id,
        payload_sha256=payload_sha256,
        payload_summary=payload_summary,
        status="PROCESSING",
        attempt_count=1,
        first_seen_at=now,
        last_seen_at=now,
        processing_started_at=now,
    )
    created = False
    try:
        async with db.begin_nested():
            db.add(event)
            await db.flush()
            created = True
    except IntegrityError:
        event = (
            await db.execute(
                select(PaymentWebhookEvent)
                .where(
                    PaymentWebhookEvent.provider == provider,
                    PaymentWebhookEvent.event_key == event_key,
                )
                .with_for_update()
            )
        ).scalar_one()

    if created:
        return WebhookClaim(event=event, should_process=True, duplicate=False)

    event.attempt_count = int(event.attempt_count or 0) + 1
    event.last_seen_at = now
    if event.status in FINAL_STATUSES:
        await db.flush()
        return WebhookClaim(event=event, should_process=False, duplicate=True)

    max_attempts = max(1, min(int(settings.payment_webhook_max_attempts), 50))
    if event.attempt_count > max_attempts:
        event.status = "DEAD_LETTER"
        event.processed_at = now
        event.response_code = 200
        event.error_code = "max_attempts_exceeded"
        await db.flush()
        return WebhookClaim(event=event, should_process=False, duplicate=True)

    timeout = timedelta(
        seconds=max(30, min(int(settings.payment_webhook_processing_timeout_seconds), 3600))
    )
    started = _utc(event.processing_started_at)
    if event.status == "PROCESSING" and started and now - started < timeout:
        await db.flush()
        return WebhookClaim(event=event, should_process=False, duplicate=True)

    event.status = "PROCESSING"
    event.processing_started_at = now
    event.processed_at = None
    event.response_code = None
    event.error_code = None
    event.payload_sha256 = payload_sha256
    event.payload_summary = payload_summary
    await db.flush()
    return WebhookClaim(event=event, should_process=True, duplicate=True)


async def link_event_to_payment(
    db: AsyncSession,
    event_id: int,
    payment_id: int,
) -> PaymentWebhookEvent:
    event = await db.get(PaymentWebhookEvent, event_id)
    if not event:
        raise RuntimeError("payment webhook ledger event not found")
    event.payment_id = payment_id
    await db.flush()
    return event


async def finish_webhook_event(
    db: AsyncSession,
    event_id: int,
    *,
    status: str,
    response_code: int,
    error_code: str | None = None,
) -> PaymentWebhookEvent:
    event = await db.get(PaymentWebhookEvent, event_id)
    if not event:
        raise RuntimeError("payment webhook ledger event not found")
    event.status = status
    event.response_code = int(response_code)
    event.error_code = error_code
    event.processed_at = datetime.now(timezone.utc)
    await db.flush()
    return event
