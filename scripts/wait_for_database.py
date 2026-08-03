from __future__ import annotations

import asyncio
import json
import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.session import engine


async def wait_for_database() -> dict[str, object]:
    timeout = max(1, int(settings.database_startup_wait_seconds))
    deadline = time.monotonic() + timeout
    attempts = 0
    last_error: str | None = None

    try:
        while True:
            attempts += 1
            try:
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                return {
                    "ok": True,
                    "operation": "database-wait",
                    "attempts": attempts,
                }
            except (SQLAlchemyError, OSError, TimeoutError) as error:
                last_error = type(error).__name__

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {
                    "ok": False,
                    "operation": "database-wait",
                    "attempts": attempts,
                    "error_type": last_error or "DatabaseUnavailable",
                }
            await asyncio.sleep(min(2.0, remaining))
    finally:
        await engine.dispose()


def main() -> int:
    result = asyncio.run(wait_for_database())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
