import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db.migrations import run_database_migrations
from app.db.session import AsyncSessionLocal
from app.models.admin_user import AdminUser
from app.models.lawyer import Lawyer
from app.security.access_control import hash_password
from app.system.settings_service import SettingsService


async def get_or_create_lawyer(db):
    result = await db.execute(
        select(Lawyer).where(Lawyer.email == "lawyer@example.com")
    )
    lawyer = result.scalars().first()
    if not lawyer:
        lawyer = Lawyer(
            full_name="Дежурный юрист",
            email="lawyer@example.com",
            specialization="ДДУ 214-ФЗ",
        )
        db.add(lawyer)
    return lawyer


async def get_or_create_admin(db):
    result = await db.execute(
        select(AdminUser).where(AdminUser.email == "admin@example.com")
    )
    admin = result.scalars().first()
    initial_password = settings.admin_password or settings.admin_api_token
    if not admin:
        admin = AdminUser(
            full_name="Владелец системы",
            username=settings.admin_username or "admin",
            email="admin@example.com",
            password_hash=hash_password(initial_password),
            role="superadmin,admin",
            is_active=True,
        )
        db.add(admin)
    else:
        admin.username = admin.username or settings.admin_username or "admin"
        admin.role = "superadmin,admin"
        admin.is_active = True
        if not admin.password_hash or admin.password_hash == "dev":
            admin.password_hash = hash_password(initial_password)
    return admin


async def bootstrap_data() -> None:
    async with AsyncSessionLocal() as db:
        if settings.bootstrap_demo_data:
            await get_or_create_lawyer(db)
        if settings.bootstrap_admin:
            await get_or_create_admin(db)
        await SettingsService(db).bootstrap_defaults()
        await db.commit()


def main() -> None:
    run_database_migrations()
    asyncio.run(bootstrap_data())
    print("База данных обновлена и инициализирована")


if __name__ == "__main__":
    main()
