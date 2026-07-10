import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from sqlalchemy import text
from app.config import settings
from app.db.session import AsyncSessionLocal


def check_file(path: str) -> tuple[bool, str]:
    p = ROOT / path
    return p.exists(), path


async def check_db() -> tuple[bool, str]:
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("select 1"))
        return True, "database connection"
    except Exception as exc:
        return False, f"database connection: {exc}"


async def main() -> int:
    checks = []
    for f in [".env.example", "pyproject.toml", "app/main.py", "scripts/init_db.py", "README.md"]:
        checks.append(check_file(f))

    checks.append((not settings.run_bot or settings.bot_token not in (None, "", "CHANGE_ME"), "BOT_TOKEN задан, если RUN_BOT=true"))
    checks.append((settings.admin_api_token not in (None, "", "dev-admin-token") or settings.app_env == "local", "ADMIN_API_TOKEN задан или local-dev"))
    checks.append(await check_db())

    ok_all = True
    print("\nПроверка проекта Digital Legal Concierge Bot\n")
    for ok, name in checks:
        mark = "OK" if ok else "FAIL"
        print(f"[{mark}] {name}")
        ok_all = ok_all and ok

    print("\nПодсказка:")
    print("1. cp .env.example .env")
    print("2. python scripts/init_db.py")
    print("3. python scripts/e2e_smoke.py")
    print("4. python -m app.main")
    print("5. открыть http://localhost:8000/admin-ui")

    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
