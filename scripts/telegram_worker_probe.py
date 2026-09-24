from __future__ import annotations

import asyncio
import json
import os

from aiogram import Bot
from sqlalchemy import text

from app.bot.lease import POSTGRES_TELEGRAM_LOCK_KEY
from app.db.session import engine


async def main() -> int:
    expected_username = str(
        os.environ.get("TELEGRAM_EXPECTED_USERNAME") or ""
    ).strip().lstrip("@")

    bot = Bot(token=os.environ["BOT_TOKEN"])
    try:
        identity = await asyncio.wait_for(bot.get_me(), timeout=15)
    finally:
        await bot.session.close()

    lock_is_held = False
    async with engine.connect() as connection:
        acquired = bool(
            (
                await connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": POSTGRES_TELEGRAM_LOCK_KEY},
                )
            ).scalar_one()
        )
        if acquired:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": POSTGRES_TELEGRAM_LOCK_KEY},
            )
            await connection.commit()
        else:
            lock_is_held = True

    result = {
        "ok": bool(identity.username) and lock_is_held,
        "bot_id": identity.id,
        "bot_username": identity.username,
        "expected_username": expected_username or None,
        "username_matches": (
            not expected_username or identity.username == expected_username
        ),
        "polling_lease_held": lock_is_held,
    }
    result["ok"] = bool(result["ok"] and result["username_matches"])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
