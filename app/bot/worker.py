from __future__ import annotations

import asyncio
import json

from app.bot.bot import run_bot
from scripts.production_preflight import build_report
from scripts.wait_for_database import wait_for_database
from scripts.wait_for_redis import wait_for_redis


async def main() -> None:
    preflight = build_report()
    print(json.dumps(preflight, ensure_ascii=False, sort_keys=True), flush=True)
    if not preflight.get("ok"):
        raise RuntimeError("Telegram worker production preflight failed")

    database = await wait_for_database()
    print(json.dumps(database, ensure_ascii=False, sort_keys=True), flush=True)
    if not database.get("ok"):
        raise RuntimeError("Telegram worker cannot reach PostgreSQL")

    redis = await wait_for_redis()
    print(json.dumps(redis, ensure_ascii=False, sort_keys=True), flush=True)
    if not redis.get("ok"):
        raise RuntimeError("Telegram worker cannot reach Redis")

    await run_bot()


if __name__ == "__main__":
    asyncio.run(main())
