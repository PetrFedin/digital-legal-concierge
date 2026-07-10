import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.models.case import Case
from app.models.payment import Payment

router = APIRouter(prefix="/webhooks/payments", tags=["payment-webhooks"])


def verify_signature(raw_body: bytes, signature: str | None) -> None:
    # In dev empty/missing signature is allowed only when APP_ENV=local.
    if settings.app_env == "local" and not signature:
        return
    expected = hmac.new(settings.payment_webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="bad webhook signature")


@router.post("/fake")
async def fake_payment_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_payment_signature: str | None = Header(default=None),
):
    raw_body = await request.body()
    verify_signature(raw_body, x_payment_signature)

    try:
        payload = json.loads(raw_body.decode() or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "invalid json") from exc

    payment_id = payload.get("payment_id")
    status = payload.get("status")

    if not payment_id:
        raise HTTPException(400, "payment_id required")

    payment = (await db.execute(select(Payment).where(Payment.id == int(payment_id)))).scalars().first()
    if not payment:
        raise HTTPException(404, "payment not found")

    case = (await db.execute(select(Case).where(Case.id == payment.case_id))).scalars().first()
    if not case:
        raise HTTPException(404, "case not found")

    service = PaymentWebhookService(db)
    if status == "paid":
        await service.process_successful_payment(payment=payment, case=case, provider_payload=payload)
    elif status == "failed":
        await service.process_failed_payment(payment=payment, case=case, provider_payload=payload)
    else:
        raise HTTPException(400, "unknown payment status")

    await db.commit()
    return {"ok": True, "payment_id": payment.id, "status": payment.status, "case_status": case.status}

@router.get('/fake-pay/{payment_id}')
async def fake_payment_page(payment_id: int, db: AsyncSession = Depends(get_db)):
    payment = (await db.execute(select(Payment).where(Payment.id == int(payment_id)))).scalars().first()
    if not payment:
        raise HTTPException(404, 'payment not found')
    return HTMLResponse(f'''
    <!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Оплата</title>
    <style>body{{font-family:Arial,sans-serif;max-width:640px;margin:40px auto;padding:0 20px}}.card{{border:1px solid #ddd;border-radius:16px;padding:24px}}button{{font-size:18px;padding:14px 20px;border:0;border-radius:12px;background:#111;color:white;cursor:pointer}}.muted{{color:#666}}</style></head>
    <body><div class="card"><h1>Тестовая оплата</h1>
    <p><b>{payment.title}</b></p><p>Сумма: {payment.amount} {payment.currency}</p>
    <p class="muted">Это локальная страница оплаты для проверки сценария. В production подключается реальный платежный провайдер.</p>
    <form method="post" action="/webhooks/payments/fake-pay/{payment.id}/success"><button type="submit">Подтвердить оплату</button></form>
    </div></body></html>
    ''')

@router.post('/fake-pay/{payment_id}/success')
async def fake_payment_success(payment_id: int, db: AsyncSession = Depends(get_db)):
    payment = (await db.execute(select(Payment).where(Payment.id == int(payment_id)))).scalars().first()
    if not payment:
        raise HTTPException(404, 'payment not found')
    case = (await db.execute(select(Case).where(Case.id == payment.case_id))).scalars().first()
    if not case:
        raise HTTPException(404, 'case not found')
    await PaymentWebhookService(db).process_successful_payment(payment=payment, case=case, provider_payload={'source':'fake_payment_page'})
    await db.commit()
    return HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><body style="font-family:Arial;max-width:640px;margin:40px auto"><h1>Оплата подтверждена</h1><p>Вернитесь в Telegram-бот и откройте «Мое дело».</p></body></html>')

@router.post('/yookassa')
async def yookassa_payment_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Webhook YooKassa.

    Обрабатывает payment.succeeded/payment.canceled. Для боевого контура рекомендуется
    дополнительно ограничить endpoint на уровне reverse proxy и сверять payment_id через API провайдера.
    """
    payload = await request.json()
    obj = payload.get('object') or {}
    provider_payment_id = obj.get('id')
    event = payload.get('event')
    if not provider_payment_id:
        raise HTTPException(400, 'provider payment id required')

    payment = (await db.execute(select(Payment).where(Payment.provider_payment_id == provider_payment_id))).scalars().first()
    if not payment:
        raise HTTPException(404, 'payment not found')
    case = (await db.execute(select(Case).where(Case.id == payment.case_id))).scalars().first()
    if not case:
        raise HTTPException(404, 'case not found')

    service = PaymentWebhookService(db)
    if event == 'payment.succeeded' or obj.get('status') == 'succeeded':
        await service.process_successful_payment(payment=payment, case=case, provider_payload=payload)
    elif event == 'payment.canceled' or obj.get('status') == 'canceled':
        await service.process_failed_payment(payment=payment, case=case, provider_payload=payload)
    else:
        return {'ok': True, 'ignored': True, 'event': event}
    await db.commit()
    return {'ok': True, 'payment_id': payment.id, 'case_status': case.status}

@router.get('/payment-result')
async def payment_result(payment_id: int | None = None):
    return HTMLResponse('''<!doctype html><html lang="ru"><meta charset="utf-8"><body style="font-family:Arial;max-width:680px;margin:40px auto;padding:0 20px"><h1>Платеж принят в обработку</h1><p>Вернитесь в Telegram-бот и нажмите «Проверить оплату» или откройте «Мое дело». Статус обновится после подтверждения платежной системой.</p></body></html>''')
