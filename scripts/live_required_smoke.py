from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from decimal import Decimal
from urllib.parse import urlparse

from aiogram import Bot

from app.config import settings
from app.domain.payments.providers import YooKassaPaymentProvider


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for LIVE_REQUIRED")
    return value


def _required_chat_id(name: str) -> int:
    raw = _required_env(name)
    try:
        chat_id = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer Telegram chat id") from exc
    if chat_id == 0:
        raise RuntimeError(f"{name} must not be zero")
    return chat_id


async def _telegram_smoke() -> None:
    token = _required_env("BOT_TOKEN")
    admin_chat_id = _required_chat_id("LIVE_TELEGRAM_ADMIN_CHAT_ID")
    client_chat_id = _required_chat_id("LIVE_TELEGRAM_CLIENT_CHAT_ID")
    if admin_chat_id == client_chat_id:
        raise RuntimeError(
            "LIVE_TELEGRAM_ADMIN_CHAT_ID and LIVE_TELEGRAM_CLIENT_CHAT_ID must be distinct"
        )

    bot = Bot(token=token)
    sent_messages: list[tuple[int, int]] = []
    cleanup_error: Exception | None = None
    try:
        identity = await bot.get_me()
        if not identity.is_bot or not identity.id:
            raise RuntimeError("Telegram getMe did not return a valid bot identity")

        for role, chat_id in (
            ("admin", admin_chat_id),
            ("client", client_chat_id),
        ):
            message = await bot.send_message(
                chat_id=chat_id,
                text=(
                    "Digital Legal Concierge LIVE_REQUIRED connectivity smoke "
                    f"({role}). No action is required."
                ),
                disable_notification=True,
            )
            if int(message.chat.id) != chat_id or not message.message_id:
                raise RuntimeError(
                    f"Telegram did not confirm delivery to the configured {role} test chat"
                )
            sent_messages.append((chat_id, int(message.message_id)))

        print(
            "LIVE_REQUIRED Telegram OK: "
            f"bot_id={identity.id}; delivered_test_chats=2"
        )
    finally:
        for chat_id, message_id in reversed(sent_messages):
            try:
                deleted = await bot.delete_message(chat_id=chat_id, message_id=message_id)
                if deleted is not True:
                    raise RuntimeError("Telegram deleteMessage returned a non-success result")
            except Exception as exc:  # pragma: no cover - exercised only by live API
                cleanup_error = cleanup_error or exc
        await bot.session.close()
        if cleanup_error is not None:
            raise RuntimeError(
                "Telegram LIVE_REQUIRED smoke delivered a message but could not clean it up"
            ) from cleanup_error


async def _provider_smoke() -> None:
    if str(settings.payment_provider or "").strip().lower() != "yookassa":
        raise RuntimeError("PAYMENT_PROVIDER must be yookassa for LIVE_REQUIRED")

    _required_env("YOOKASSA_SHOP_ID")
    _required_env("YOOKASSA_SECRET_KEY")
    amount_minor_raw = _required_env("LIVE_PROVIDER_AMOUNT_MINOR")
    currency = _required_env("LIVE_PROVIDER_CURRENCY").upper()

    try:
        amount_minor = int(amount_minor_raw)
    except ValueError as exc:
        raise RuntimeError("LIVE_PROVIDER_AMOUNT_MINOR must be an integer") from exc
    if not 1 <= amount_minor <= 10_000:
        raise RuntimeError(
            "LIVE_PROVIDER_AMOUNT_MINOR must be between 1 and 10000 for the sandbox smoke"
        )
    if len(currency) != 3 or not currency.isalpha():
        raise RuntimeError("LIVE_PROVIDER_CURRENCY must be a three-letter currency code")

    public_base_url = str(settings.public_base_url or "").strip().rstrip("/")
    parsed_base = urlparse(public_base_url)
    if parsed_base.scheme != "https" or not parsed_base.netloc:
        raise RuntimeError("PUBLIC_BASE_URL must be an absolute HTTPS URL for LIVE_REQUIRED")

    marker = uuid.uuid4().int
    payment_id = 9_000_000_000 + (marker % 900_000_000)
    case_id = 8_000_000_000 + (marker % 900_000_000)
    amount = (Decimal(amount_minor) / Decimal("100")).quantize(Decimal("0.01"))

    # This deliberately exercises the production provider adapter and its
    # idempotence-key/redirect construction. The confirmation URL is never opened,
    # so the smoke creates only an unpaid provider-side test payment.
    provider = YooKassaPaymentProvider()
    result = await provider.create_payment(
        payment_id=payment_id,
        amount=amount,
        currency=currency,
        title="Digital Legal Concierge LIVE_REQUIRED sandbox smoke",
        metadata={
            "case_id": str(case_id),
            "live_required": "true",
        },
    )

    confirmation = urlparse(result.payment_url)
    if not result.provider_payment_id:
        raise RuntimeError("YooKassa did not return a provider payment id")
    if confirmation.scheme != "https" or not confirmation.netloc:
        raise RuntimeError("YooKassa did not return an HTTPS confirmation URL")

    raw = result.raw or {}
    if raw.get("test") is not True:
        raise RuntimeError(
            "YooKassa response is not marked test=true; LIVE_REQUIRED refuses to "
            "accept production-shop credentials as sandbox evidence"
        )

    print(
        "LIVE_REQUIRED YooKassa test-shop OK: "
        f"provider_payment_id={result.provider_payment_id}; confirmation_not_opened=true"
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
