from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.lawyer import Lawyer


async def main() -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Lawyer).where(Lawyer.email == "lawyer@example.local"))
        lawyer = result.scalars().first()
        if not lawyer:
            lawyer = Lawyer(full_name="Демо Юрист", email="lawyer@example.local", is_active=True, workload_limit=30)
            db.add(lawyer)
            await db.commit()
            print("Создан демо-юрист: lawyer@example.local")
        else:
            print("Демо-юрист уже есть")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
