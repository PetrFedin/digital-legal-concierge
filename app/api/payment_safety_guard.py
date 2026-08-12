from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.payment_webhooks import (
    fake_payment_page as legacy_fake_payment_page,
    fake_payment_success as legacy_fake_payment_success,
    fake_payment_webhook as legacy_fake_payment_webhook,
)
from app.config import settings
from app.db.session import get_db

router = APIRouter(tags=["payment-safety-guard"])


def require_local_test_fake_endpoint() -> None:
    # Never let DEMO_MODE or an accidental PAYMENT_PROVIDER=fake expose the
    # simulated payment surface in staging/production. Keep legacy fake routes
    # usable only for local automated/manual development checks.
    if settings.app_env not in {"local", "test"}:
        raise HTTPException(status_code=404, detail="not found")


@router.post("/webhooks/payments/fake")
async def guarded_fake_payment_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_payment_signature: str | None = Header(default=None),
    x_payment_event_id: str | None = Header(default=None),
):
    require_local_test_fake_endpoint()
    return await legacy_fake_payment_webhook(
        request=request,
        db=db,
        x_payment_signature=x_payment_signature,
        x_payment_event_id=x_payment_event_id,
    )


@router.get("/webhooks/payments/fake-pay/{payment_id}")
async def guarded_fake_payment_page(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
):
    require_local_test_fake_endpoint()
    return await legacy_fake_payment_page(payment_id=payment_id, db=db)


@router.post("/webhooks/payments/fake-pay/{payment_id}/success")
async def guarded_fake_payment_success(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
):
    require_local_test_fake_endpoint()
    return await legacy_fake_payment_success(payment_id=payment_id, db=db)
