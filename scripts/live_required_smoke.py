from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from urllib.parse import urlparse

from aiogram import Bot

from app.config import settings
from app.domain.payments.providers import YooKassaPaymentProvider


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for LIVE_REQUIRED")
    return value


async def _telegram_smoke() -> None:
    token = _required_env("BOT_TOKEN")
    bot = Bot(token=token)
    try:
        identity = await bot.get_me()
        if not identity.is_bot or not identity.id:
            raise RuntimeError("Telegram getMe did not return a valid bot identity")
        print(f"LIVE_REQUIRED Telegram OK: bot_id={identity.id}")
    finally:
        await bot.session.close()


async def _provider_smoke() -> None:
    if settings.payment_provider.strip().lower() != "yookassa":
        raise RuntimeError("PAYMENT_PROVIDER must be yookassa for LIVE_REQUIRED")

    _required_env("YOOKASSA_SHOP_ID")
    _required_env("YOOKASSA_SECRET_KEY")
    amount_raw = _required_env("LIVE_PROVIDER_AMOUNT_MINOR")
    currency = _required_env("LIVE_PROVIDER_CURRENCY").upper()

    try:
        amount = int(amount_raw)
    except ValueError as exc:
        raise RuntimeError("LIVE_PROVIDER_AMOUNT_MINOR must be an integer") from exc
    if amount <= 0:
        raise RuntimeError("LIVE_PROVIDER_AMOUNT_MINOR must be positive")
    if len(currency) != 3 or not currency.isalpha():
        raise RuntimeError("LIVE_PROVIDER_CURRENCY must be a three-letter currency code")

    public_base_url = str(settings.public_base_url or "").strip().rstrip("/")
    parsed_base = urlparse(public_base_url)
    if parsed_base.scheme != "https" or not parsed_base.netloc:
        raise RuntimeError("PUBLIC_BASE_URL must be an absolute HTTPS URL for LIVE_REQUIRED")

    marker = uuid.uuid4().int
    payment_id = 9_000_000_000 + (marker % 900_000_000)
    case_id = 8_000_000_000 + (marker % 900_000_000)
    idempotency_key = f"live-required-{uuid.uuid4()}"
    return_url = f"{public_base_url}/payment/return?payment_id={payment_id}"

    provider = YooKassaPaymentProvider()
    result = await provider.create_payment(
        payment_id=payment_id,
        case_id=case_id,
        amount=amount,
        currency=currency,
        idempotency_key=idempotency_key,
        return_url=return_url,
    )

    confirmation = urlparse(result.confirmation_url)
    if not result.payment_id:
        raise RuntimeError("YooKassa did not return a provider payment id")
    if confirmation.scheme != "https" or not confirmation.netloc:
        raise RuntimeError("YooKassa did not return an HTTPS confirmation URL")

    print(
        "LIVE_REQUIRED YooKassa test-shop OK: "
        f"provider_payment_id={result.payment_id}; confirmation_not_opened=true"
    )


async def _run(target: str) -> None:
    if target == "telegram":
        await _telegram_smoke()
        return
    if target == "provider":
        await _provider_smoke()
        return
    raise RuntimeError(f"Unsupported LIVE_REQUIRED target: {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail-closed LIVE_REQUIRED external smokes")
    parser.add_argument("target", choices=("telegram", "provider"))
    args = parser.parse_args()
    asyncio.run(_run(args.target))


if __name__ == "__main__":
    main()
