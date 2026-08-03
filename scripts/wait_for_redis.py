from __future__ import annotations

import asyncio
import json
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings


async def wait_for_redis() -> dict[str, object]:
    if settings.fsm_storage_backend != "redis":
        return {
            "ok": True,
            "operation": "redis-wait",
            "skipped": True,
            "reason": "fsm-storage-is-not-redis",
        }

    timeout = max(1, int(settings.redis_startup_wait_seconds))
    deadline = time.monotonic() + timeout
    attempts = 0
    last_error: str | None = None

    while True:
        attempts += 1
        client = Redis.from_url(settings.redis_url)
        try:
            if await client.ping():
                return {
                    "ok": True,
                    "operation": "redis-wait",
                    "skipped": False,
                    "attempts": attempts,
                }
        except (RedisError, OSError, TimeoutError) as error:
            last_error = type(error).__name__
        finally:
            await client.aclose()

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "ok": False,
                "operation": "redis-wait",
                "attempts": attempts,
                "error_type": last_error or "RedisUnavailable",
            }
        await asyncio.sleep(min(2.0, remaining))


def main() -> int:
    result = asyncio.run(wait_for_redis())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
