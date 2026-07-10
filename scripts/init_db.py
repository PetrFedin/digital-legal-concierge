import asyncio
import sys
from pathlib import Path

from sqlalchemy import select, text

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.lawyer import Lawyer
from app.security.access_control import hash_password
from app.system.settings_service import SettingsService


async def get_or_create_lawyer(db):
    result = await db.execute(select(Lawyer).where(Lawyer.email == "lawyer@example.com"))
    lawyer = result.scalars().first()
    if not lawyer:
        db.add(
            Lawyer(
                full_name="Дежурный юрист",
                email="lawyer@example.com",
                specialization="ДДУ 214-ФЗ",
            )
        )


async def table_columns(db, table_name: str) -> set[str]:
    rows = (await db.execute(text(f"PRAGMA table_info({table_name})"))).all()
    return {row[1] for row in rows}


async def migrate_admin_users(db):
    if not settings.database_url.startswith("sqlite"):
        return
    columns = await table_columns(db, "admin_users")
    if not columns:
        return
    if "username" not in columns:
        await db.execute(text("ALTER TABLE admin_users ADD COLUMN username VARCHAR(100)"))
    if "telegram_id" not in columns:
        await db.execute(text("ALTER TABLE admin_users ADD COLUMN telegram_id BIGINT"))
    await db.commit()


async def migrate_consultations(db):
    if not settings.database_url.startswith("sqlite"):
        return
    columns = await table_columns(db, "consultations")
    if not columns:
        return
    additions = {
        "related_case_id": "INTEGER",
        "slot_id": "INTEGER",
        "subject_type": "VARCHAR(50) DEFAULT 'new_or_other'",
    }
    for name, ddl in additions.items():
        if name not in columns:
            await db.execute(text(f"ALTER TABLE consultations ADD COLUMN {name} {ddl}"))
    await db.execute(
        text(
            "UPDATE consultations SET subject_type='new_or_other' "
            "WHERE subject_type IS NULL OR subject_type=''"
        )
    )
    await db.commit()


async def get_or_create_admin(db):
    result = await db.execute(select(AdminUser).where(AdminUser.email == "admin@example.com"))
    admin = result.scalars().first()
    initial_password = settings.admin_password or settings.admin_api_token
    if not admin:
        db.add(
            AdminUser(
                full_name="Владелец системы",
                username=settings.admin_username or "admin",
                email="admin@example.com",
                password_hash=hash_password(initial_password),
                role="superadmin,admin",
                is_active=True,
            )
        )
    else:
        admin.username = admin.username or settings.admin_username or "admin"
        admin.role = "superadmin,admin"
        admin.is_active = True
        if not admin.password_hash or admin.password_hash == "dev":
            admin.password_hash = hash_password(initial_password)


async def main():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await migrate_admin_users(db)
        await migrate_consultations(db)
        await get_or_create_lawyer(db)
        await get_or_create_admin(db)
        await SettingsService(db).bootstrap_defaults()
        await db.commit()
    print("База данных инициализирована")


if __name__ == "__main__":
    asyncio.run(main())
