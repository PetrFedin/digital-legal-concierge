from __future__ import annotations

import asyncio
import json

from app.bot.bot import run_bot
from app.scheduler.notification_dispatcher import NotificationDispatcher
from app.scheduler.scheduler import AppScheduler
from scripts.production_preflight import build_report
from scripts.wait_for_database import wait_for_database
from scripts.wait_for_redis import wait_for_redis


async def _supervise_background_runtime() -> None:
    dispatcher = NotificationDispatcher()
    scheduler = AppScheduler()

    tasks = {
        asyncio.create_task(run_bot(), name="telegram-bot"): "telegram-bot",
        asyncio.create_task(
            dispatcher.run_forever(),
            name="notification-dispatcher",
        ): "notification-dispatcher",
        asyncio.create_task(
            scheduler.run_forever(),
            name="scheduler",
        ): "scheduler",
    }

    try:
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )
        failed = min(done, key=lambda task: tasks[task])
        name = tasks[failed]

        if failed.cancelled():
            raise RuntimeError(f"{name} was cancelled unexpectedly")

        error = failed.exception()
        if error is not None:
            raise RuntimeError(f"{name} failed") from error

        raise RuntimeError(f"{name} exited unexpectedly")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    preflight = build_report()
    print(json.dumps(preflight, ensure_ascii=False, sort_keys=True), flush=True)
    if not preflight.get("ok"):
        raise RuntimeError("Background worker production preflight failed")

    database = await wait_for_database()
    print(json.dumps(database, ensure_ascii=False, sort_keys=True), flush=True)
    if not database.get("ok"):
        raise RuntimeError("Background worker cannot reach PostgreSQL")

    redis = await wait_for_redis()
    print(json.dumps(redis, ensure_ascii=False, sort_keys=True), flush=True)
    if not redis.get("ok"):
        raise RuntimeError("Background worker cannot reach Redis")

    await _supervise_background_runtime()


if __name__ == "__main__":
    asyncio.run(main())
