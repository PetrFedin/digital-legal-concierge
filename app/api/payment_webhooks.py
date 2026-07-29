import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.payments.providers import YooKassaPaymentProvider
from app.models.case import Case
from app.models.payment import Payment

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


def decimal_amount(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise HTTPException(409, "Некорректная сумма в ответе провайдера") from error


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
            raise HTTPException(
                409,
                f"Метаданные платежа не совпадают: {key}",
            )


async def load_payment_and_case(db: AsyncSession, payment_id: int):
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "payment not found")
    case = await db.get(Case, payment.case_id)
    if not case:
        raise HTTPException(404, "case not found")
    return payment, case


@router.post("/fake")
async def fake_payment_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_payment_signature: str | None = Header(default=None),
):
    require_fake_payments()
    raw_body = await request.body()
    verify_signature(raw_body, x_payment_signature)

    try:
        payload = json.loads(raw_body.decode() or "{}")
    except json.JSONDecodeError as error:
        raise HTTPException(400, "invalid json") from error

    payment_id = payload.get("payment_id")
    status = payload.get("status")
    if not payment_id:
        raise HTTPException(400, "payment_id required")

    payment, case = await load_payment_and_case(db, int(payment_id))
    if payment.provider not in {None, "fake"}:
        raise HTTPException(409, "Платеж создан другим провайдером")

    service = PaymentWebhookService(db)
    if status == "paid":
        await service.process_successful_payment(
            payment=payment,
            case=case,
            provider_payload=payload,
        )
    elif status == "failed":
        await service.process_failed_payment(
            payment=payment,
            case=case,
            provider_payload=payload,
        )
    else:
        raise HTTPException(400, "unknown payment status")

    await db.commit()
    return {
        "ok": True,
        "payment_id": payment.id,
        "status": payment.status,
        "case_status": case.status,
    }


@router.get("/fake-pay/{payment_id}")
async def fake_payment_page(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
):
    require_fake_payments()
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "payment not found")
    if payment.provider not in {None, "fake"}:
        raise HTTPException(409, "Платеж создан другим провайдером")
    return HTMLResponse(
        f"""
        <!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Оплата</title>
        <style>body{{font-family:Arial,sans-serif;max-width:640px;margin:40px auto;padding:0 20px}}.card{{border:1px solid #ddd;border-radius:16px;padding:24px}}button{{font-size:18px;padding:14px 20px;border:0;border-radius:12px;background:#111;color:white;cursor:pointer}}.muted{{color:#666}}</style></head>
        <body><div class="card"><h1>Тестовая оплата</h1>
        <p><b>{payment.title}</b></p><p>Сумма: {payment.amount} {payment.currency}</p>
        <p class="muted">Локальная страница для проверки сценария.</p>
        <form method="post" action="/webhooks/payments/fake-pay/{payment.id}/success"><button type="submit">Подтвердить оплату</button></form>
        </div></body></html>
        """
    )


@router.post("/fake-pay/{payment_id}/success")
async def fake_payment_success(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
):
    require_fake_payments()
    payment, case = await load_payment_and_case(db, payment_id)
    if payment.provider not in {None, "fake"}:
        raise HTTPException(409, "Платеж создан другим провайдером")
    await PaymentWebhookService(db).process_successful_payment(
        payment=payment,
        case=case,
        provider_payload={"source": "fake_payment_page"},
    )
    await db.commit()
    status_text = (
        "Оплата подтверждена. Вернитесь в Telegram-бот."
        if payment.status == "PAID"
        else "Оплата получена и передана администратору на проверку."
    )
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

    payload = await request.json()
    webhook_object = payload.get("object") or {}
    provider_payment_id = webhook_object.get("id")
    if not provider_payment_id:
        raise HTTPException(400, "provider payment id required")

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
    verified_status = verified.get("status")
    service = PaymentWebhookService(db)
    if verified_status == "succeeded" and verified.get("paid") is True:
        await service.process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"webhook": payload, "verified": verified},
        )
    elif verified_status == "canceled":
        await service.process_failed_payment(
            payment=payment,
            case=case,
            provider_payload={"webhook": payload, "verified": verified},
        )
    else:
        return {
            "ok": True,
            "ignored": True,
            "status": verified_status,
        }

    await db.commit()
    return {
        "ok": True,
        "payment_id": payment.id,
        "payment_status": payment.status,
        "case_status": case.status,
    }


@router.get("/payment-result")
async def payment_result(payment_id: int | None = None):
    return HTMLResponse(
        """<!doctype html><html lang="ru"><meta charset="utf-8"><body style="font-family:Arial;max-width:680px;margin:40px auto;padding:0 20px"><h1>Платеж принят в обработку</h1><p>Вернитесь в Telegram-бот и откройте «Мое дело». Статус обновится после подтверждения платежной системой.</p></body></html>"""
    )
