import hashlib
import hmac
import html
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.payments.providers import YooKassaPaymentProvider
from app.models.case import Case
from app.models.payment import Payment
from app.models.payment_webhook_event import PaymentWebhookEvent
from app.security.payment_webhook_ledger import (
    WebhookClaim,
    canonical_payload_sha256,
    claim_webhook_event,
    derive_event_key,
    finish_webhook_event,
    link_event_to_payment,
    read_limited_body,
)
from app.security.security_events import (
    record_security_event,
    record_security_event_best_effort,
)

router = APIRouter(prefix="/webhooks/payments", tags=["payment-webhooks"])


def fake_payments_enabled() -> bool:
    return settings.payment_provider == "fake" and (
        settings.app_env in {"local", "test"} or settings.demo_mode
    )


def require_fake_payments() -> None:
    if not fake_payments_enabled():
        raise HTTPException(status_code=404, detail="not found")


def verify_signature(raw_body: bytes, signature: str | None) -> None:
    if settings.app_env in {"local", "test"} and not signature:
        return
    expected = hmac.new(
        settings.payment_webhook_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="bad webhook signature")


def parse_json_object(raw_body: bytes) -> dict:
    try:
        value = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(400, "invalid json") from error
    if not isinstance(value, dict):
        raise HTTPException(400, "json object required")
    return value


def decimal_amount(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise HTTPException(409, "Некорректная сумма в ответе провайдера") from error


def parse_provider_datetime(value: object) -> datetime | None:
    """Parse an external ISO-8601 fact time without trusting host timezone.

    Missing or malformed provider timestamps are not invented. Callers pass
    ``None`` to the lifecycle service, which then records our UTC processing
    time as the explicit fallback business timestamp.
    """

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_verified_yookassa_payment(payment: Payment, verified: dict) -> None:
    if verified.get("id") != payment.provider_payment_id:
        raise HTTPException(409, "Идентификатор платежа провайдера не совпадает")

    amount = verified.get("amount") or {}
    if decimal_amount(amount.get("value")) != decimal_amount(payment.amount):
        raise HTTPException(409, "Сумма платежа не совпадает")
    if str(amount.get("currency") or "").upper() != payment.currency.upper():
        raise HTTPException(409, "Валюта платежа не совпадает")

    metadata = verified.get("metadata") or {}
    expected = {
        "internal_payment_id": str(payment.id),
        "case_id": str(payment.case_id),
        "payment_code": str(payment.payment_code),
        "reservation_key": str(payment.reservation_key or ""),
    }
    for key, expected_value in expected.items():
        if str(metadata.get(key, "")) != expected_value:
            raise HTTPException(409, f"Метаданные платежа не совпадают: {key}")


def fake_payload_summary(payload: dict) -> dict:
    return {
        "event_id": str(payload.get("event_id") or "")[:100],
        "payment_id": str(payload.get("payment_id") or "")[:30],
        "status": str(payload.get("status") or "")[:32],
    }


def yookassa_payload_summary(payload: dict, verified: dict | None = None) -> dict:
    webhook_object = (
        payload.get("object") if isinstance(payload.get("object"), dict) else {}
    )
    amount = (
        webhook_object.get("amount")
        if isinstance(webhook_object.get("amount"), dict)
        else {}
    )
    result = {
        "event": str(payload.get("event") or "")[:100],
        "provider_payment_id": str(webhook_object.get("id") or "")[:255],
        "webhook_status": str(webhook_object.get("status") or "")[:32],
        "amount": str(amount.get("value") or "")[:32],
        "currency": str(amount.get("currency") or "")[:10],
    }
    if verified:
        verified_amount = (
            verified.get("amount")
            if isinstance(verified.get("amount"), dict)
            else {}
        )
        result["verified"] = {
            "status": str(verified.get("status") or "")[:32],
            "paid": bool(verified.get("paid")),
            "amount": str(verified_amount.get("value") or "")[:32],
            "currency": str(verified_amount.get("currency") or "")[:10],
            "captured_at": str(verified.get("captured_at") or "")[:64],
        }
    return result


async def load_payment_and_case(db: AsyncSession, payment_id: int):
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "payment not found")
    case = await db.get(Case, payment.case_id)
    if not case:
        raise HTTPException(404, "case not found")
    return payment, case


async def duplicate_response(
    request: Request,
    claim: WebhookClaim,
    *,
    provider: str,
):
    event = claim.event
    if claim.payload_conflict:
        await record_security_event_best_effort(
            action="security.payment_webhook_replay_mismatch",
            severity="critical",
            source=f"{provider}_payment_webhook",
            client_address=request.client.host if request.client else None,
            resource_type="payment_webhook_event",
            resource_id=event.id,
            details={
                "provider": provider,
                "event_type": event.event_type,
                "attempt_count": event.attempt_count,
            },
            comment="Повторный идентификатор webhook получен с изменённым телом",
            sample_seconds=1,
        )
        return JSONResponse(
            {
                "ok": False,
                "duplicate": True,
                "payload_conflict": True,
                "ledger_status": event.status,
            },
            status_code=409,
        )
    return JSONResponse(
        {
            "ok": True,
            "duplicate": True,
            "ledger_status": event.status,
            "attempt_count": event.attempt_count,
        },
        status_code=200,
    )


async def mark_event_failure(
    db: AsyncSession,
    event_id: int,
    *,
    status_code: int,
    error_code: str,
    terminal: bool,
) -> None:
    await db.rollback()
    await finish_webhook_event(
        db,
        event_id,
        status="DEAD_LETTER" if terminal else "FAILED",
        response_code=status_code,
        error_code=error_code,
    )
    await db.commit()


@router.post("/fake")
async def fake_payment_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_payment_signature: str | None = Header(default=None),
    x_payment_event_id: str | None = Header(default=None),
):
    require_fake_payments()
    try:
        raw_body = await read_limited_body(request)
        verify_signature(raw_body, x_payment_signature)
    except HTTPException as error:
        await record_security_event_best_effort(
            action="security.payment_webhook_rejected",
            severity="warning",
            source="fake_payment_webhook",
            client_address=request.client.host if request.client else None,
            details={"status_code": error.status_code, "reason": str(error.detail)},
            comment="Отклонён тестовый платёжный webhook",
            sample_seconds=1,
        )
        raise
    payload = parse_json_object(raw_body)
    payment_id = payload.get("payment_id")
    status = str(payload.get("status") or "")
    if not payment_id:
        raise HTTPException(400, "payment_id required")
    if status not in {"paid", "failed"}:
        raise HTTPException(400, "unknown payment status")

    digest = canonical_payload_sha256(payload)
    external_event_id = str(x_payment_event_id or payload.get("event_id") or "")[:100]
    event_key = derive_event_key(
        "fake",
        external_event_id or digest,
        payment_id,
        status,
    )
    claim = await claim_webhook_event(
        db,
        provider="fake",
        event_key=event_key,
        event_type=f"payment.{status}",
        provider_payment_id=str(payment_id),
        payload_sha256=digest,
        payload_summary=fake_payload_summary(payload),
    )
    event_id = claim.event.id
    if not claim.should_process:
        await db.commit()
        return await duplicate_response(request, claim, provider="fake")
    await record_security_event(
        db,
        action="security.payment_webhook_received",
        severity="info",
        source="fake_payment_webhook",
        client_address=request.client.host if request.client else None,
        resource_type="payment_webhook_event",
        resource_id=event_id,
        details={"provider": "fake", "event_type": f"payment.{status}"},
        comment="Принят новый платёжный webhook в идемпотентный ledger",
    )
    await db.commit()

    try:
        payment, case = await load_payment_and_case(db, int(payment_id))
        if payment.provider not in {None, "fake"}:
            raise HTTPException(409, "Платеж создан другим провайдером")
        await link_event_to_payment(db, event_id, payment.id)
        service = PaymentWebhookService(db)
        summary = fake_payload_summary(payload)
        if status == "paid":
            await service.process_successful_payment(
                payment=payment,
                case=case,
                provider_payload=summary,
            )
        else:
            await service.process_failed_payment(
                payment=payment,
                case=case,
                provider_payload=summary,
            )
        await finish_webhook_event(
            db,
            event_id,
            status="PROCESSED",
            response_code=200,
        )
        response = {
            "ok": True,
            "payment_id": int(payment.id),
            "status": str(payment.status),
            "case_status": str(case.status),
            "duplicate": False,
        }
        await db.commit()
        return response
    except HTTPException as error:
        await mark_event_failure(
            db,
            event_id,
            status_code=error.status_code,
            error_code="fake_validation_failed",
            terminal=error.status_code < 500,
        )
        raise
    except Exception:
        await mark_event_failure(
            db,
            event_id,
            status_code=500,
            error_code="fake_processing_error",
            terminal=False,
        )
        raise


@router.get("/fake-pay/{payment_id}")
async def fake_payment_page(payment_id: int, db: AsyncSession = Depends(get_db)):
    require_fake_payments()
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "payment not found")
    if payment.provider not in {None, "fake"}:
        raise HTTPException(409, "Платеж создан другим провайдером")
    title = html.escape(str(payment.title))
    amount = html.escape(str(payment.amount))
    currency = html.escape(str(payment.currency))
    return HTMLResponse(
        f"""
        <!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Оплата</title>
        <style>body{{font-family:Arial,sans-serif;max-width:640px;margin:40px auto;padding:0 20px}}.card{{border:1px solid #ddd;border-radius:16px;padding:24px}}button{{font-size:18px;padding:14px 20px;border:0;border-radius:12px;background:#111;color:white;cursor:pointer}}.muted{{color:#666}}</style></head>
        <body><div class="card"><h1>Тестовая оплата</h1>
        <p><b>{title}</b></p><p>Сумма: {amount} {currency}</p>
        <p class="muted">Локальная страница для проверки сценария.</p>
        <form method="post" action="/webhooks/payments/fake-pay/{payment.id}/success"><button type="submit">Подтвердить оплату</button></form>
        </div></body></html>
        """
    )


@router.post("/fake-pay/{payment_id}/success")
async def fake_payment_success(payment_id: int, db: AsyncSession = Depends(get_db)):
    require_fake_payments()
    payment, case = await load_payment_and_case(db, payment_id)
    if payment.provider not in {None, "fake"}:
        raise HTTPException(409, "Платеж создан другим провайдером")
    await PaymentWebhookService(db).process_successful_payment(
        payment=payment,
        case=case,
        provider_payload={"source": "fake_payment_page"},
    )
    status_text = (
        "Оплата подтверждена. Вернитесь в Telegram-бот."
        if str(payment.status) == "PAID"
        else "Оплата получена и передана администратору на проверку."
    )
    await db.commit()
    return HTMLResponse(
        "<!doctype html><html lang='ru'><meta charset='utf-8'>"
        "<body style='font-family:Arial;max-width:640px;margin:40px auto'>"
        f"<h1>{status_text}</h1></body></html>"
    )


@router.post("/yookassa")
async def yookassa_payment_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if settings.payment_provider != "yookassa":
        raise HTTPException(404, "not found")
    try:
        raw_body = await read_limited_body(request)
        payload = parse_json_object(raw_body)
    except HTTPException as error:
        await record_security_event_best_effort(
            action="security.payment_webhook_rejected",
            severity="warning",
            source="yookassa_payment_webhook",
            client_address=request.client.host if request.client else None,
            details={"status_code": error.status_code, "reason": str(error.detail)},
            comment="Отклонён платёжный webhook YooKassa",
            sample_seconds=1,
        )
        raise

    webhook_object = payload.get("object")
    if not isinstance(webhook_object, dict):
        raise HTTPException(400, "webhook object required")
    provider_payment_id = str(webhook_object.get("id") or "")
    event_type = str(payload.get("event") or "")[:100]
    webhook_status = str(webhook_object.get("status") or "")[:32]
    if not provider_payment_id:
        raise HTTPException(400, "provider payment id required")
    if not event_type:
        raise HTTPException(400, "event type required")

    digest = canonical_payload_sha256(payload)
    event_key = derive_event_key(
        "yookassa",
        event_type,
        provider_payment_id,
        webhook_status,
    )
    claim = await claim_webhook_event(
        db,
        provider="yookassa",
        event_key=event_key,
        event_type=event_type,
        provider_payment_id=provider_payment_id,
        payload_sha256=digest,
        payload_summary=yookassa_payload_summary(payload),
    )
    event_id = claim.event.id
    if not claim.should_process:
        await db.commit()
        return await duplicate_response(request, claim, provider="yookassa")
    await record_security_event(
        db,
        action="security.payment_webhook_received",
        severity="info",
        source="yookassa_payment_webhook",
        client_address=request.client.host if request.client else None,
        resource_type="payment_webhook_event",
        resource_id=event_id,
        details={"provider": "yookassa", "event_type": event_type},
        comment="Принят новый платёжный webhook в идемпотентный ledger",
    )
    await db.commit()

    try:
        payment = (
            await db.execute(
                select(Payment).where(
                    Payment.provider_payment_id == provider_payment_id
                )
            )
        ).scalars().first()
        if not payment:
            raise HTTPException(404, "payment not found")
        case = await db.get(Case, payment.case_id)
        if not case:
            raise HTTPException(404, "case not found")
        await link_event_to_payment(db, event_id, payment.id)

        try:
            verified = await YooKassaPaymentProvider().retrieve_payment(
                provider_payment_id
            )
        except httpx.HTTPError as error:
            raise HTTPException(
                502,
                "Не удалось перепроверить платеж через API YooKassa",
            ) from error

        validate_verified_yookassa_payment(payment, verified)
        ledger_event = await db.get(PaymentWebhookEvent, event_id)
        if not ledger_event:
            raise RuntimeError("payment webhook ledger event not found")
        ledger_event.payload_summary = yookassa_payload_summary(payload, verified)
        verified_status = verified.get("status")
        service = PaymentWebhookService(db)
        summary = yookassa_payload_summary(payload, verified)
        if verified_status == "succeeded" and verified.get("paid") is True:
            await service.process_successful_payment(
                payment=payment,
                case=case,
                provider_payload=summary,
                occurred_at=parse_provider_datetime(verified.get("captured_at")),
            )
            ledger_status = "PROCESSED"
        elif verified_status == "canceled":
            await service.process_failed_payment(
                payment=payment,
                case=case,
                provider_payload=summary,
            )
            ledger_status = "PROCESSED"
        else:
            ledger_status = "IGNORED"

        await finish_webhook_event(
            db,
            event_id,
            status=ledger_status,
            response_code=200,
        )
        if ledger_status == "IGNORED":
            response = {
                "ok": True,
                "ignored": True,
                "status": str(verified_status or ""),
                "duplicate": False,
            }
        else:
            response = {
                "ok": True,
                "payment_id": int(payment.id),
                "payment_status": str(payment.status),
                "case_status": str(case.status),
                "duplicate": False,
            }
        await db.commit()
        return response
    except HTTPException as error:
        terminal = error.status_code in {400, 401, 403, 409}
        await mark_event_failure(
            db,
            event_id,
            status_code=error.status_code,
            error_code=(
                "yookassa_validation_failed"
                if terminal
                else "yookassa_retryable_error"
            ),
            terminal=terminal,
        )
        if terminal:
            await record_security_event_best_effort(
                action="security.payment_webhook_validation_failed",
                severity="critical",
                source="yookassa_payment_webhook",
                client_address=request.client.host if request.client else None,
                resource_type="payment_webhook_event",
                resource_id=event_id,
                details={"status_code": error.status_code, "event_type": event_type},
                comment="Webhook не совпал с авторитетными данными провайдера",
                sample_seconds=1,
            )
        raise
    except Exception:
        await mark_event_failure(
            db,
            event_id,
            status_code=500,
            error_code="yookassa_processing_error",
            terminal=False,
        )
        raise


@router.get("/payment-result")
async def payment_result(payment_id: int | None = None):
    return HTMLResponse(
        """<!doctype html><html lang="ru"><meta charset="utf-8"><body style="font-family:Arial;max-width:680px;margin:40px auto;padding:0 20px"><h1>Платеж принят в обработку</h1><p>Вернитесь в Telegram-бот и откройте «Мое дело». Статус обновится после подтверждения платежной системой.</p></body></html>"""
    )
